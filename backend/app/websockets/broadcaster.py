import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
from redis.asyncio import Redis

from ..observability.metrics import (
    record_websocket_authentication_failure,
    record_websocket_connection_attempt,
    record_websocket_frame_sent,
    record_websocket_send_failure,
)
from ..security.redaction import sanitize_error_text

logger = logging.getLogger("logsentinel.broadcaster")


@dataclass(frozen=True, slots=True)
class WebSocketClient:
    """Immutable authorization context attached to a WebSocket."""

    socket: WebSocket
    tenant_id: str | None
    user_id: int | None
    subscriptions: frozenset[str] = field(default_factory=frozenset)


class HighLoadBroadcaster:
    """Dynamically throttled WebSocket broadcaster with debouncing and batching."""

    def __init__(self, frame_rate_ms: float = 250.0):
        self._connections: dict[WebSocket, WebSocketClient] = {}
        self._lock = asyncio.Lock()
        self._buffer: list[dict[str, Any]] = []
        self._frame_rate_ms = frame_rate_ms
        self._task: asyncio.Task[None] | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self.redis_client: Redis | None = None
        self.channel_name = "logsentinel:telemetry:pubsub"

    def set_redis_client(self, redis_client: Redis) -> None:
        """Set the Redis client for pub/sub."""
        self.redis_client = redis_client

    def start(self):
        """Start the background flush loop and pub/sub listener."""
        if self._task and not self._task.done():
            pass
        else:
            self._task = asyncio.create_task(
                self._flush_loop(), name="websocket-broadcaster-flush"
            )

        if self._listener_task and not self._listener_task.done():
            pass
        else:
            self._listener_task = asyncio.create_task(
                self.listen_to_redis_pubsub(), name="websocket-pubsub-listener"
            )

    async def stop(self):
        """Stop the background loops."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            finally:
                self._listener_task = None

    async def connect(
        self,
        websocket: WebSocket,
        *,
        tenant_id: str | None = None,
        user_id: int | None = None,
        require_tenant: bool = False,
    ) -> None:
        if require_tenant and (not tenant_id or user_id is None):
            raise ValueError(
                "authenticated WebSocket connections require tenant and user context"
            )
        async with self._lock:
            self._connections[websocket] = WebSocketClient(
                socket=websocket,
                tenant_id=tenant_id,
                user_id=user_id,
            )

        # Ensure the loop is running when a client is connected
        self.start()

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(websocket, None)

    def connection_count(self) -> int:
        return len(self._connections)

    def record_connection_attempt(self) -> None:
        """Route hook for one WebSocket handshake attempt."""
        record_websocket_connection_attempt()

    def record_authentication_failure(self) -> None:
        """Route hook for one rejected WebSocket authentication attempt."""
        record_websocket_authentication_failure()

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Publish the event to Redis Pub/Sub."""
        tenant_id = _event_tenant_id(event)
        owner_user_id = _event_owner_user_id(event)
        event_type = str(event.get("type", ""))
        if (not tenant_id or owner_user_id is None) and not event_type.startswith(
            "system."
        ):
            async with self._lock:
                if any(client.tenant_id for client in self._connections.values()):
                    raise ValueError(
                        "tenant_id and owner_user_id are required for operational telemetry"
                    )

        if not self.redis_client:
            # Fallback to local buffer if Redis is not configured
            async with self._lock:
                if self._connections:
                    self._buffer.append(event)
            return

        try:
            payload = json.dumps(event)
            await self.redis_client.publish(self.channel_name, payload)
        except Exception as exc:
            logger.error(
                "Failed to publish telemetry event to Redis exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )
            # Fallback to local buffer on error
            async with self._lock:
                if self._connections:
                    self._buffer.append(event)

    async def listen_to_redis_pubsub(self) -> None:
        """Background loop that listens to Redis Pub/Sub and buffers events."""
        if not self.redis_client:
            return

        while True:
            try:
                pubsub = self.redis_client.pubsub()
                await pubsub.subscribe(self.channel_name)
                logger.info("WebSocket Broadcaster subscribed to %s", self.channel_name)

                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            data = message["data"]
                            if isinstance(data, bytes):
                                data = data.decode("utf-8")
                            event = json.loads(data)

                            if (
                                not _event_tenant_id(event)
                                or _event_owner_user_id(event) is None
                            ) and not str(event.get("type", "")).startswith("system."):
                                logger.warning("Dropping tenantless telemetry event")
                                continue
                            async with self._lock:
                                if self._connections:
                                    self._buffer.append(event)
                        except Exception as exc:
                            logger.error(
                                "Failed to process pubsub message exception_type=%s detail=%s",
                                type(exc).__name__,
                                sanitize_error_text(exc),
                            )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "Redis Pub/Sub listener disconnected; retrying exception_type=%s detail=%s",
                    type(exc).__name__,
                    sanitize_error_text(exc),
                )
                await asyncio.sleep(1)

    async def _flush_loop(self) -> None:
        """Background loop that drains the buffer at the configured frame rate."""
        sleep_seconds = self._frame_rate_ms / 1000.0
        while True:
            try:
                await asyncio.sleep(sleep_seconds)

                async with self._lock:
                    if not self._buffer:
                        continue

                    batch = list(self._buffer)
                    self._buffer.clear()
                    connections = list(self._connections.values())

                if not connections or not batch:
                    continue

                # Consolidate payload
                consolidated_payload = {
                    "type": "frame_update",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {"events": batch},
                }

                stale_connections: list[WebSocket] = []
                for client in connections:
                    try:
                        scoped_events = [
                            event
                            for event in batch
                            if (
                                str(event.get("type", "")).startswith("system.")
                                or (
                                    _event_tenant_id(event) == client.tenant_id
                                    and _event_owner_user_id(event) == client.user_id
                                )
                            )
                        ]
                        if not scoped_events:
                            continue
                        await client.socket.send_json(
                            {
                                **consolidated_payload,
                                "payload": {"events": scoped_events},
                            }
                        )
                        record_websocket_frame_sent()
                    except Exception as exc:
                        record_websocket_send_failure()
                        stale_connections.append(client.socket)
                        logger.error(
                            "Failed to send consolidated telemetry frame to WebSocket client exception_type=%s detail=%s",
                            type(exc).__name__,
                            sanitize_error_text(exc),
                        )

                if stale_connections:
                    async with self._lock:
                        for websocket in stale_connections:
                            self._connections.pop(websocket, None)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "Unexpected error in broadcaster flush loop exception_type=%s detail=%s",
                    type(exc).__name__,
                    sanitize_error_text(exc),
                )


def _event_tenant_id(event: dict[str, Any]) -> str | None:
    value = event.get("tenant_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _event_owner_user_id(event: dict[str, Any]) -> int | None:
    value = event.get("owner_user_id")
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else None
    )

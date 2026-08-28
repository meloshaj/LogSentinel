import asyncio
import json
import logging
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

logger = logging.getLogger("logsentinel.broadcaster")


class HighLoadBroadcaster:
    """Dynamically throttled WebSocket broadcaster with debouncing and batching."""

    def __init__(self, frame_rate_ms: float = 250.0):
        self._connections: set[WebSocket] = set()
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

    async def connect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.add(websocket)

        # Ensure the loop is running when a client is connected
        self.start()

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

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
        if not self.redis_client:
            # Fallback to local buffer if Redis is not configured
            async with self._lock:
                if self._connections:
                    self._buffer.append(event)
            return

        try:
            payload = json.dumps(event)
            await self.redis_client.publish(self.channel_name, payload)
        except Exception:
            logger.exception("Failed to publish telemetry event to Redis")
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

                            async with self._lock:
                                if self._connections:
                                    self._buffer.append(event)
                        except Exception:
                            logger.exception("Failed to process pubsub message")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Redis Pub/Sub listener disconnected. Retrying...")
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
                    connections = list(self._connections)

                if not connections or not batch:
                    continue

                # Consolidate payload
                consolidated_payload = {
                    "type": "frame_update",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {"events": batch},
                }

                stale_connections: list[WebSocket] = []
                for websocket in connections:
                    try:
                        await websocket.send_json(consolidated_payload)
                        record_websocket_frame_sent()
                    except Exception:
                        record_websocket_send_failure()
                        stale_connections.append(websocket)
                        logger.exception(
                            "Failed to send consolidated telemetry frame to WebSocket client"
                        )

                if stale_connections:
                    async with self._lock:
                        for websocket in stale_connections:
                            self._connections.discard(websocket)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in broadcaster flush loop")

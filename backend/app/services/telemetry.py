"""Lightweight WebSocket telemetry fanout for LogSentinel."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("logsentinel.telemetry")


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp for telemetry events."""
    return datetime.now(timezone.utc).isoformat()


def telemetry_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build the common telemetry event envelope."""
    return {
        "type": event_type,
        "timestamp": utc_timestamp(),
        "payload": payload,
    }


class TelemetryConnectionManager:
    """Track active telemetry WebSocket clients and broadcast JSON events."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    def connection_count(self) -> int:
        return len(self._connections)

    async def broadcast(self, event: dict[str, Any]) -> None:
        async with self._lock:
            connections = list(self._connections)

        if not connections:
            return

        stale_connections: list[WebSocket] = []
        for websocket in connections:
            try:
                await websocket.send_json(event)
            except Exception:
                stale_connections.append(websocket)
                logger.exception("Failed to send telemetry event to WebSocket client")

        if stale_connections:
            async with self._lock:
                for websocket in stale_connections:
                    self._connections.discard(websocket)


telemetry_manager = TelemetryConnectionManager()

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("logsentinel.broadcaster")


class HighLoadBroadcaster:
    """Dynamically throttled WebSocket broadcaster with debouncing and batching."""

    def __init__(self, frame_rate_ms: float = 250.0):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._buffer: list[dict[str, Any]] = []
        self._frame_rate_ms = frame_rate_ms
        self._task: asyncio.Task[None] | None = None

    def start(self):
        """Start the background flush loop."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._flush_loop(), name="websocket-broadcaster-flush")

    async def stop(self):
        """Stop the background flush loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
            
        # Ensure the loop is running when a client is connected
        self.start()

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    def connection_count(self) -> int:
        return len(self._connections)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Buffer the event instead of sending immediately to protect client rendering."""
        async with self._lock:
            if not self._connections:
                return # Don't buffer if no clients are listening
            self._buffer.append(event)
            
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
                    "events": batch
                }
                
                stale_connections: list[WebSocket] = []
                for websocket in connections:
                    try:
                        await websocket.send_json(consolidated_payload)
                    except Exception:
                        stale_connections.append(websocket)
                        logger.exception("Failed to send consolidated telemetry frame to WebSocket client")

                if stale_connections:
                    async with self._lock:
                        for websocket in stale_connections:
                            self._connections.discard(websocket)
                            
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in broadcaster flush loop")

"""In-memory parsed-log batching for the Drain3 pipeline."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("logsentinel.batch_manager")


class ParsedLogBatchManager:
    """Buffer parsed logs and flush them in fixed-size batches."""

    def __init__(
        self,
        batch_size: int = 500,
        flush_interval_seconds: float = 5.0,
        sink: Callable[[list[dict[str, Any]]], Any] | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")

        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds
        self.sink = sink
        self._buffer: list[dict[str, Any]] = []
        self._flushed_batches: list[list[dict[str, Any]]] = []
        self._failed_batches: list[list[dict[str, Any]]] = []
        self._flushed_batch_count = 0
        self._flushed_record_count = 0
        self._last_flush_at: str | None = None
        self._last_flush_record_count = 0
        self._last_sink_result: Any = None
        self._last_sink_error: str | None = None
        self._lock = asyncio.Lock()
        self._periodic_task: asyncio.Task[None] | None = None
        self._periodic_flush_count = 0
        self._shutdown_flush_count = 0

    async def add(self, parsed_log: dict[str, Any]) -> None:
        """Add one parsed log and auto-flush when the batch is full."""
        batch: list[dict[str, Any]] | None = None
        async with self._lock:
            self._buffer.append(parsed_log)
            if len(self._buffer) >= self.batch_size:
                batch = self._drain_buffer()

        if batch is not None:
            await self._flush_batch(batch)

    def start_periodic_flush(self) -> None:
        """Start periodic flushing for sub-batch traffic."""
        if self.flush_interval_seconds <= 0:
            return
        if self._periodic_task and not self._periodic_task.done():
            return

        self._periodic_task = asyncio.create_task(
            self._periodic_flush_loop(),
            name="parsed-log-periodic-flush",
        )

    async def stop_periodic_flush(self) -> None:
        """Stop the periodic flush task."""
        if not self._periodic_task:
            return

        self._periodic_task.cancel()
        try:
            await self._periodic_task
        except asyncio.CancelledError:
            pass
        finally:
            self._periodic_task = None

    async def shutdown_flush(self) -> None:
        """Flush remaining records during graceful application shutdown."""
        if self.size() == 0:
            return

        await self.flush()
        self._shutdown_flush_count += 1

    async def flush(self) -> None:
        """Flush all currently buffered logs."""
        batch: list[dict[str, Any]] | None = None
        async with self._lock:
            if self._buffer:
                batch = self._drain_buffer()

        if batch is not None:
            await self._flush_batch(batch)

    def size(self) -> int:
        """Return the current buffered record count."""
        return len(self._buffer)

    def get_stats(self) -> dict[str, Any]:
        """Return batch manager counters for diagnostics."""
        return {
            "batch_size": self.batch_size,
            "current_buffer_size": len(self._buffer),
            "flushed_batch_count": self._flushed_batch_count,
            "flushed_record_count": self._flushed_record_count,
            "last_flush_at": self._last_flush_at,
            "sink_configured": self.sink is not None,
            "last_flush_record_count": self._last_flush_record_count,
            "last_sink_result": self._last_sink_result,
            "last_sink_error": self._last_sink_error,
            "periodic_flush_enabled": self._periodic_task is not None and not self._periodic_task.done(),
            "flush_interval_seconds": self.flush_interval_seconds,
            "periodic_flush_count": self._periodic_flush_count,
            "shutdown_flush_count": self._shutdown_flush_count,
            "failed_batch_count": len(self._failed_batches),
        }

    def get_flushed_batches(self) -> list[list[dict[str, Any]]]:
        """Return in-memory flushed batches for testing and local debugging."""
        return [list(batch) for batch in self._flushed_batches]

    def get_failed_batches(self) -> list[list[dict[str, Any]]]:
        """Return failed batches retained for inspection after sink errors."""
        return [list(batch) for batch in self._failed_batches]

    def _drain_buffer(self) -> list[dict[str, Any]]:
        batch = list(self._buffer)
        self._buffer.clear()
        return batch

    async def _periodic_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval_seconds)
            if self.size() == 0:
                continue

            await self.flush()
            self._periodic_flush_count += 1

    async def _flush_batch(self, batch: list[dict[str, Any]]) -> None:
        try:
            if self.sink is None:
                self._flushed_batches.append(list(batch))
                result: Any = {"stored_in_memory": True, "record_count": len(batch)}
            else:
                result = self.sink(list(batch))
                if inspect.isawaitable(result):
                    result = await result
        except Exception as exc:
            self._failed_batches.append(list(batch))
            self._last_sink_result = None
            self._last_sink_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Parsed log batch sink failed; retained failed batch in memory")
            return

        self._flushed_batch_count += 1
        self._flushed_record_count += len(batch)
        self._last_flush_record_count = len(batch)
        self._last_sink_result = result
        self._last_sink_error = None
        self._last_flush_at = datetime.now(timezone.utc).isoformat()

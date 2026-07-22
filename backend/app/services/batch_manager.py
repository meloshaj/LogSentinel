"""In-memory parsed-log batching for the Drain3 pipeline."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("logsentinel.batch_manager")

ParsedLogRecord = dict[str, Any]
BatchSink = Callable[[list[ParsedLogRecord]], Any]


class ParsedLogBatchManager:
    """Buffer parsed logs and serialize all flushes through one async path.

    Production sinks should be asynchronous. Synchronous sinks remain supported
    temporarily for compatibility, but they are invoked on the event-loop thread
    and therefore must not perform blocking work.
    """

    def __init__(
        self,
        batch_size: int = 500,
        flush_interval_seconds: float = 5.0,
        sink: BatchSink | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")

        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds
        self.sink = sink

        self._buffer: list[ParsedLogRecord] = []
        self._flushed_batches: list[list[ParsedLogRecord]] = []
        self._last_failed_batch: list[ParsedLogRecord] | None = None

        self._flushed_batch_count = 0
        self._flushed_record_count = 0
        self._failed_flush_attempt_count = 0
        self._cancelled_flush_attempt_count = 0
        self._last_flush_at: str | None = None
        self._last_flush_record_count = 0
        self._last_sink_result: Any = None
        self._last_sink_error: str | None = None
        self._flush_active = False

        # State changes are brief and never include a sink await. The flush lock
        # covers selection, sink invocation, restoration, and final accounting.
        self._state_lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()

        self._periodic_task: asyncio.Task[None] | None = None
        self._periodic_stop_event = asyncio.Event()
        self._periodic_flush_count = 0
        self._shutdown_flush_count = 0

    async def add(self, parsed_log: ParsedLogRecord) -> None:
        """Add one parsed log and trigger a serialized threshold flush."""
        async with self._state_lock:
            self._buffer.append(parsed_log)
            should_flush = len(self._buffer) >= self.batch_size

        if should_flush:
            await self.flush()

    def start_periodic_flush(self) -> None:
        """Start periodic flushing for traffic below the batch threshold."""
        if self.flush_interval_seconds <= 0:
            return
        if self._periodic_task and not self._periodic_task.done():
            return

        self._periodic_stop_event.clear()
        self._periodic_task = asyncio.create_task(
            self._periodic_flush_loop(),
            name="parsed-log-periodic-flush",
        )

    async def stop_periodic_flush(self) -> None:
        """Stop periodic flushing without cancelling an in-flight sink call."""
        task = self._periodic_task
        if task is None:
            return

        self._periodic_stop_event.set()
        try:
            await task
        finally:
            if self._periodic_task is task:
                self._periodic_task = None

    async def shutdown_flush(self) -> None:
        """Flush pending records through the same serialized flush path."""
        if await self.flush():
            async with self._state_lock:
                self._shutdown_flush_count += 1

    async def flush(self) -> bool:
        """Flush all currently pending logs.

        Returns ``True`` only when a non-empty batch is successfully flushed.
        Sink failures are recorded and leave the attempted records pending.
        Cancellation is recorded, the records are restored, and cancellation is
        then propagated to the caller.
        """
        async with self._flush_lock:
            async with self._state_lock:
                if not self._buffer:
                    return False
                batch = self._drain_buffer()
                self._flush_active = True

            try:
                result = await self._invoke_sink(batch)
            except asyncio.CancelledError:
                await self._restore_failed_batch(
                    batch,
                    error_summary="CancelledError: sink invocation cancelled",
                    cancelled=True,
                )
                logger.warning(
                    "Parsed log batch sink was cancelled; restored %d pending records",
                    len(batch),
                )
                raise
            except Exception as exc:
                await self._restore_failed_batch(
                    batch,
                    error_summary=_safe_error_summary(exc),
                    cancelled=False,
                )
                logger.error(
                    "Parsed log batch sink failed with %s; restored %d pending records",
                    type(exc).__name__,
                    len(batch),
                )
                return False

            async with self._state_lock:
                if self.sink is None:
                    self._flushed_batches.append(list(batch))
                self._flushed_batch_count += 1
                self._flushed_record_count += len(batch)
                self._last_flush_record_count = len(batch)
                self._last_sink_result = result
                self._last_sink_error = None
                self._last_failed_batch = None
                self._last_flush_at = datetime.now(timezone.utc).isoformat()
                self._flush_active = False
            return True

    def size(self) -> int:
        """Return an event-loop-local snapshot of the pending record count."""
        return len(self._buffer)

    def get_stats(self) -> dict[str, Any]:
        """Return an event-loop-local snapshot of manager diagnostics."""
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
            "periodic_flush_enabled": self._periodic_task is not None
            and not self._periodic_task.done(),
            "flush_interval_seconds": self.flush_interval_seconds,
            "periodic_flush_count": self._periodic_flush_count,
            "shutdown_flush_count": self._shutdown_flush_count,
            # Keep the legacy name while exposing its corrected meaning.
            "failed_batch_count": self._failed_flush_attempt_count,
            "failed_flush_attempt_count": self._failed_flush_attempt_count,
            "cancelled_flush_attempt_count": self._cancelled_flush_attempt_count,
            "flush_in_progress": self._flush_active,
        }

    def get_pending_records(self) -> list[ParsedLogRecord]:
        """Return a shallow snapshot of records still awaiting persistence."""
        return list(self._buffer)

    def get_flushed_batches(self) -> list[list[ParsedLogRecord]]:
        """Return in-memory flushed batches for testing and local debugging."""
        return [list(batch) for batch in self._flushed_batches]

    def get_failed_batches(self) -> list[list[ParsedLogRecord]]:
        """Return the latest failed attempt for backward-compatible inspection.

        Failed records live in the active pending buffer. Only one bounded
        diagnostic snapshot is retained here, avoiding an unbounded secondary
        failure store.
        """
        if self._last_failed_batch is None:
            return []
        return [list(self._last_failed_batch)]

    def _drain_buffer(self) -> list[ParsedLogRecord]:
        """Drain the active buffer while the caller holds the state lock."""
        batch = list(self._buffer)
        self._buffer.clear()
        return batch

    async def _invoke_sink(self, batch: list[ParsedLogRecord]) -> Any:
        if self.sink is None:
            return {"stored_in_memory": True, "record_count": len(batch)}

        result = self.sink(list(batch))
        if inspect.isawaitable(result):
            return await result
        return result

    async def _restore_failed_batch(
        self,
        batch: list[ParsedLogRecord],
        *,
        error_summary: str,
        cancelled: bool,
    ) -> None:
        """Restore an attempted batch ahead of records added during its flush."""
        async with self._state_lock:
            self._buffer[0:0] = batch
            self._failed_flush_attempt_count += 1
            if cancelled:
                self._cancelled_flush_attempt_count += 1
            self._last_failed_batch = list(batch)
            self._last_sink_result = None
            self._last_sink_error = error_summary
            self._flush_active = False

    async def _periodic_flush_loop(self) -> None:
        while not self._periodic_stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._periodic_stop_event.wait(),
                    timeout=self.flush_interval_seconds,
                )
            except TimeoutError:
                if await self.flush():
                    async with self._state_lock:
                        self._periodic_flush_count += 1


def _safe_error_summary(exc: Exception) -> str:
    """Return a short error summary with common credential forms redacted."""
    message = " ".join(str(exc).split())
    message = re.sub(
        r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^@\s]+)@",
        r"\1<redacted>@",
        message,
    )
    message = re.sub(
        r"(?i)\b(password|passwd|pwd|token|secret)=([^&\s]+)",
        r"\1=<redacted>",
        message,
    )
    if len(message) > 200:
        message = f"{message[:197]}..."
    exception_name = type(exc).__name__
    return f"{exception_name}: {message}" if message else exception_name

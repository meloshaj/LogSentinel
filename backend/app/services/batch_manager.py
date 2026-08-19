"""In-memory parsed-log batching for the Drain3 pipeline."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..models import ParsedLog

logger = logging.getLogger("logsentinel.batch_manager")

BatchSink = Callable[[list[ParsedLog]], Any]

MAX_BATCH_SIZE = 1000
MAX_FLUSH_INTERVAL_MS = 250
MAX_BUFFER_CAPACITY = 50000

class ParsedLogBatchManager:
    """
    Buffer parsed logs and serialize all flushes through one async path.
    Implements a strict dual-trigger flush policy (size or time), capacity shedding,
    and exponential backoff retries.
    """

    def __init__(
        self,
        batch_size: int = MAX_BATCH_SIZE,
        flush_interval_seconds: float = MAX_FLUSH_INTERVAL_MS / 1000.0,
        sink: BatchSink | None = None,
        benchmarking_collector: Any = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")

        self.batch_size = min(batch_size, MAX_BATCH_SIZE)
        self.flush_interval_seconds = min(flush_interval_seconds, MAX_FLUSH_INTERVAL_MS / 1000.0)
        self.sink = sink
        self.benchmarking_collector = benchmarking_collector

        self._buffer: list[ParsedLog] = []
        self._flushed_batches: list[list[ParsedLog]] = []
        self._last_failed_batch: list[ParsedLog] | None = None

        self._flushed_batch_count = 0
        self._flushed_record_count = 0
        self._failed_flush_attempt_count = 0
        self._cancelled_flush_attempt_count = 0
        self._last_flush_at: str | None = None
        self._last_flush_record_count = 0
        self._last_sink_result: Any = None
        self._last_sink_error: str | None = None
        self._flush_active = False

        self._oldest_timestamp: float | None = None

        self._state_lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()

        self._periodic_task: asyncio.Task[None] | None = None
        self._periodic_stop_event = asyncio.Event()
        self._periodic_flush_count = 0
        self._shutdown_flush_count = 0

    async def add(self, parsed_log: ParsedLog) -> None:
        """Add one parsed log and trigger a serialized threshold flush."""
        should_flush = False
        async with self._state_lock:
            if len(self._buffer) >= MAX_BUFFER_CAPACITY:
                logger.warning(f"Buffer capacity exceeded ({MAX_BUFFER_CAPACITY}). Dropping log.")
                return
            if len(self._buffer) == 0:
                self._oldest_timestamp = time.monotonic()
            
            self._buffer.append(parsed_log)
            
            size_exceeded = len(self._buffer) >= self.batch_size
            time_exceeded = (
                self._oldest_timestamp is not None
                and (time.monotonic() - self._oldest_timestamp) >= self.flush_interval_seconds
            )
            should_flush = size_exceeded or time_exceeded

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

    async def _periodic_flush_loop(self) -> None:
        while not self._periodic_stop_event.is_set():
            try:
                # Wake up more frequently to enforce the SLA tightly
                await asyncio.sleep(0.05)
                should_flush = False
                async with self._state_lock:
                    if self._buffer and self._oldest_timestamp is not None:
                        if (time.monotonic() - self._oldest_timestamp) >= self.flush_interval_seconds:
                            should_flush = True
                
                if should_flush:
                    if await self.flush():
                        async with self._state_lock:
                            self._periodic_flush_count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic flush loop encountered error: {e}")

    async def stop_periodic_flush(self) -> None:
        """Stop periodic flushing without cancelling an in-flight sink call."""
        task = self._periodic_task
        if task is None:
            return

        self._periodic_stop_event.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            if self._periodic_task is task:
                self._periodic_task = None

    async def shutdown_flush(self) -> None:
        """Flush pending records through the same serialized flush path."""
        if await self.flush():
            async with self._state_lock:
                self._shutdown_flush_count += 1

    async def flush_all(self) -> None:
        """Graceful teardown method executed during application lifespan shutdown to drain pending in-memory records."""
        await self.shutdown_flush()

    async def flush(self) -> bool:
        """Flush all currently pending logs.
        
        Uses exponential backoff retry logic (up to 3 attempts).
        """
        async with self._flush_lock:
            async with self._state_lock:
                if not self._buffer:
                    return False
                batch = self._drain_buffer()
                self._flush_active = True

            retries = 3
            result = None
            for attempt in range(retries):
                try:
                    result = await self._invoke_sink(batch)
                    break
                except asyncio.CancelledError:
                    await self._restore_failed_batch(batch, "CancelledError: sink invocation cancelled", True)
                    logger.warning(f"Parsed log batch sink cancelled; restored {len(batch)} records")
                    raise
                except Exception as exc:
                    if attempt < retries - 1:
                        sleep_time = (2 ** attempt) * 0.1
                        logger.warning(f"Sink attempt {attempt+1} failed ({type(exc).__name__}). Retrying in {sleep_time}s")
                        await asyncio.sleep(sleep_time)
                    else:
                        await self._restore_failed_batch(batch, str(exc), False)
                        logger.error(f"Parsed log batch sink failed after 3 attempts; restored {len(batch)} records")
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
        return len(self._buffer)

    def get_stats(self) -> dict[str, Any]:
        from ..core.profiler import db_profiler
        stats = {
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
            "failed_batch_count": self._failed_flush_attempt_count,
            "failed_flush_attempt_count": self._failed_flush_attempt_count,
            "cancelled_flush_attempt_count": self._cancelled_flush_attempt_count,
            "flush_in_progress": self._flush_active,
        }
        if db_profiler.enabled:
            stats["profiling"] = db_profiler.get_profiling_summary()
        return stats

    def get_pending_records(self) -> list[ParsedLog]:
        return list(self._buffer)

    def get_flushed_batches(self) -> list[list[ParsedLog]]:
        return [list(batch) for batch in self._flushed_batches]

    def get_failed_batches(self) -> list[list[ParsedLog]]:
        if self._last_failed_batch is None:
            return []
        return [list(self._last_failed_batch)]

    def _drain_buffer(self) -> list[ParsedLog]:
        batch = list(self._buffer)
        self._buffer.clear()
        self._oldest_timestamp = None
        return batch

    async def _invoke_sink(self, batch: list[ParsedLog]) -> Any:
        start_time = time.perf_counter()
        
        if self.sink is None:
            result = {"stored_in_memory": True, "record_count": len(batch)}
        else:
            result = self.sink(list(batch))
            if inspect.isawaitable(result):
                result = await result
                
        if self.benchmarking_collector:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.benchmarking_collector.record("sink_latency_ms", duration_ms)
            
        return result

    async def _restore_failed_batch(self, batch: list[ParsedLog], error_summary: str, cancelled: bool) -> None:
        async with self._state_lock:
            self._buffer = batch + self._buffer
            if self._buffer and self._oldest_timestamp is None:
                self._oldest_timestamp = time.monotonic()
            self._last_failed_batch = batch
            self._last_sink_error = error_summary
            self._flush_active = False
            if cancelled:
                self._cancelled_flush_attempt_count += 1
            else:
                self._failed_flush_attempt_count += 1

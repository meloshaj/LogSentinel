"""Async worker that drains queued ingest payloads into Drain3 parsing."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import ParsedLog
from ..services.batch_manager import ParsedLogBatchManager
from ..services.drain_parser import DrainParser
from ..services.runtime_dependency_parser import RuntimeDependencyParser, TraceObservation
from ..services.telemetry import telemetry_event, telemetry_manager

logger = logging.getLogger("logsentinel.drain_worker")


class DrainWorker:
    """Consume ingest queue items, parse log messages, and keep recent results."""

    def __init__(
        self,
        log_buffer: Any,
        parser: DrainParser,
        batch_manager: ParsedLogBatchManager | None = None,
        recent_limit: int = 1000,
        on_log_parsed: Optional[Callable[[ParsedLog], None]] = None,
        runtime_dependency_parser: RuntimeDependencyParser | None = None,
        on_trace_observation: Optional[Callable[[TraceObservation], None]] = None,
        recent_trace_observation_limit: int = 1000,
        queue_drain_timeout_seconds: float = 30.0,
    ) -> None:
        if queue_drain_timeout_seconds <= 0:
            raise ValueError("queue_drain_timeout_seconds must be greater than 0")

        self.log_buffer = log_buffer
        self.parser = parser
        self.batch_manager = batch_manager or ParsedLogBatchManager()
        self._recent_parsed_logs: deque[ParsedLog] = deque(maxlen=recent_limit)
        self._on_log_parsed = on_log_parsed
        self.runtime_dependency_parser = runtime_dependency_parser
        self._recent_trace_observations: deque[TraceObservation] = deque(
            maxlen=recent_trace_observation_limit
        )
        self._on_trace_observation = on_trace_observation
        self.queue_drain_timeout_seconds = queue_drain_timeout_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self.processed_count = 0
        self.error_count = 0
        self.last_processed_at: str | None = None
        self.last_queue_drain_timed_out = False
        self.last_shutdown_batch_flush_failed = False

    def start(self) -> None:
        """Start the background worker without blocking application startup."""
        if self._task and not self._task.done():
            return

        self._running = True
        self.batch_manager.start_periodic_flush()
        self._task = asyncio.create_task(self.run(), name="drain-worker")

    async def stop(self) -> None:
        """Drain accepted payloads, stop the consumer, and flush parsed logs."""
        self.last_queue_drain_timed_out = False
        self.last_shutdown_batch_flush_failed = False

        task = self._task
        if task is not None:
            logger.info(
                "Starting Drain worker queue drain: queue_size=%s timeout_seconds=%.3f",
                self._queue_size(),
                self.queue_drain_timeout_seconds,
            )
            try:
                await asyncio.wait_for(
                    self.log_buffer.join(),
                    timeout=self.queue_drain_timeout_seconds,
                )
                logger.info("Drain worker queue drain completed")
            except TimeoutError:
                self.last_queue_drain_timed_out = True
                logger.error(
                    "Drain worker queue drain timed out after %.3f seconds; "
                    "remaining_queue_size=%s",
                    self.queue_drain_timeout_seconds,
                    self._queue_size(),
                )

        self._running = False
        if task is not None:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            finally:
                if self._task is task:
                    self._task = None

        await self.batch_manager.stop_periodic_flush()
        await self.batch_manager.shutdown_flush()

        batch_stats = self.batch_manager.get_stats()
        pending_records = int(batch_stats.get("current_buffer_size", 0))
        if pending_records > 0:
            self.last_shutdown_batch_flush_failed = True
            logger.error(
                "Final parsed-log batch flush did not persist all records; "
                "pending_records=%d",
                pending_records,
            )

        logger.info(
            "Drain worker stop completed: queue_drain_timed_out=%s "
            "pending_batch_records=%d",
            self.last_queue_drain_timed_out,
            pending_records,
        )

    async def run(self) -> None:
        """Continuously consume queued ingest payloads."""
        while self._running:
            try:
                item = await self.log_buffer.dequeue()
            except asyncio.CancelledError:
                # Cancellation while waiting did not dequeue a payload, so
                # there is no corresponding unfinished task to acknowledge.
                raise
            except Exception:
                self.error_count += 1
                logger.exception("Drain worker failed while dequeuing an item")
                continue

            try:
                await self.process_one(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error_count += 1
                logger.exception("Drain worker failed while processing queued item")
            finally:
                # A successful dequeue owns exactly one completion signal,
                # including when processing fails or is cancelled.
                self.log_buffer.task_done()

    async def process_one(self, item: Any) -> list[ParsedLog]:
        """Process one queued payload or log entry."""
        parsed_logs: list[ParsedLog] = []
        errors_before_extract = self.error_count

        extracted_messages = self._extract_log_messages(item)
        for raw_message, metadata in extracted_messages:
            try:
                parsed = self.parser.parse(raw_message, metadata=metadata)
            except Exception:
                self.error_count += 1
                logger.exception("Drain parser failed for log message")
                continue

            trace_observation = self._extract_trace_observation(parsed)
            self._recent_parsed_logs.append(parsed)
            await self.batch_manager.add(parsed)
            self.processed_count += 1
            self.last_processed_at = datetime.now(timezone.utc).isoformat()
            parsed_logs.append(parsed)
            self._schedule_log_parsed_event(parsed)
            if trace_observation is not None:
                self._record_trace_observation(trace_observation)
            
            # Notify subscribers (e.g., feature extraction worker)
            if self._on_log_parsed:
                try:
                    self._on_log_parsed(parsed)
                except Exception:
                    logger.exception("Log parsed callback failed")

        if not parsed_logs and self.error_count == errors_before_extract:
            self.error_count += 1
            logger.warning("Drain worker could not extract any log messages from queued item: %r", item)

        return parsed_logs

    def get_stats(self) -> dict[str, Any]:
        """Return worker counters and queue visibility."""
        return {
            "running": self._running,
            "processed_count": self.processed_count,
            "error_count": self.error_count,
            "last_processed_at": self.last_processed_at,
            "queue_size": self._queue_size(),
            "last_queue_drain_timed_out": self.last_queue_drain_timed_out,
            "last_shutdown_batch_flush_failed": self.last_shutdown_batch_flush_failed,
            "recent_parsed_count": len(self._recent_parsed_logs),
            "recent_trace_observation_count": len(self._recent_trace_observations),
            "batch": self.batch_manager.get_stats(),
        }

    def get_recent_parsed_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent parsed logs as dicts, newest first."""
        safe_limit = max(0, limit)
        recent = list(self._recent_parsed_logs)[-safe_limit:][::-1] if safe_limit else []
        return [log.model_dump(mode="json") for log in recent]

    def get_recent_trace_observations(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent trace observations as dicts, newest first."""
        safe_limit = max(0, limit)
        recent = (
            list(self._recent_trace_observations)[-safe_limit:][::-1]
            if safe_limit
            else []
        )
        return [observation.model_dump(mode="json") for observation in recent]

    def _queue_size(self) -> int | None:
        queue_size = getattr(self.log_buffer, "queue_size", None)
        if callable(queue_size):
            return int(queue_size())
        return None

    def _extract_log_messages(self, item: Any) -> list[tuple[str, dict[str, Any]]]:
        if isinstance(item, str):
            return [(item, {})]

        if not isinstance(item, dict):
            return []

        parent_metadata = self._metadata_from_payload(item)
        logs = item.get("logs")

        if isinstance(logs, list):
            extracted: list[tuple[str, dict[str, Any]]] = []
            for entry in logs:
                entry_messages = self._extract_entry(entry, parent_metadata)
                if not entry_messages:
                    self._record_unsupported(entry)
                extracted.extend(entry_messages)
            return extracted

        entry_messages = self._extract_entry(item, parent_metadata)
        if not entry_messages:
            self._record_unsupported(item)
        return entry_messages

    def _extract_entry(
        self,
        entry: Any,
        parent_metadata: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        if isinstance(entry, str):
            return [(entry, dict(parent_metadata))]

        if not isinstance(entry, dict):
            return []

        raw_message = self._find_message(entry)
        if not raw_message:
            return []

        metadata = dict(parent_metadata)
        nested_metadata = entry.get("metadata")
        if isinstance(nested_metadata, dict):
            metadata.update(nested_metadata)

        for source_key, target_key in (
            ("service_name", "service"),
            ("service", "service"),
            ("level", "level"),
            ("timestamp", "timestamp"),
            ("correlation_id", "correlation_id"),
        ):
            value = entry.get(source_key)
            if value is not None:
                metadata[target_key] = value

        return [(raw_message, metadata)]

    def _metadata_from_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in ("source", "environment", "correlation_id"):
            value = item.get(key)
            if value is not None:
                metadata[key] = value
        return metadata

    def _find_message(self, entry: dict[str, Any]) -> str | None:
        for key in ("raw_message", "raw", "message", "log"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _record_unsupported(self, item: Any) -> None:
        self.error_count += 1
        logger.warning("Drain worker found unsupported log entry shape: %r", item)

    def _schedule_log_parsed_event(self, parsed: ParsedLog) -> None:
        event = telemetry_event(
            "log.parsed",
            {
                "source": parsed.source,
                "environment": parsed.environment,
                "service": parsed.service,
                "level": parsed.level,
                "template_id": parsed.template_id,
                "template": parsed.template_text,
                "correlation_id": parsed.correlation_id,
            },
        )

        try:
            asyncio.create_task(telemetry_manager.broadcast(event))
        except RuntimeError:
            logger.debug("No running event loop available for log.parsed telemetry broadcast")

    def _extract_trace_observation(self, parsed: ParsedLog) -> TraceObservation | None:
        if self.runtime_dependency_parser is None:
            return None
        try:
            return self.runtime_dependency_parser.extract(parsed)
        except Exception:
            logger.exception("Runtime dependency trace extraction failed")
            return None

    def _record_trace_observation(self, observation: TraceObservation) -> None:
        self._recent_trace_observations.append(observation)
        if self._on_trace_observation is None:
            return
        try:
            self._on_trace_observation(observation)
        except Exception:
            logger.exception("Trace observation callback failed")

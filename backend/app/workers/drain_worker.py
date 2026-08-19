"""Async worker that drains queued ingest payloads into Drain3 parsing."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

from ..models import ParsedLog
from ..schemas.alerting import IncidentAlertPayload
from ..services.alerting import dispatch_incident_alert
from ..services.batch_manager import ParsedLogBatchManager
from ..services.drain_parser import DrainParser
from ..services.runtime_dependency_parser import (
    RuntimeDependencyParser,
    TraceObservation,
)
from ..services.telemetry import telemetry_event, telemetry_manager

logger = logging.getLogger("logsentinel.drain_worker")


class DrainWorker:
    """
    Consume ingest queue items, parse log messages, and keep recent results.
    
    This worker runs as a background task, continuously pulling raw log payloads
    from a memory buffer, processing them through Drain3, and forwarding the
    structured results to downstream systems.
    """

    def __init__(
        self,
        log_buffer: Any,
        parser: DrainParser,
        batch_manager: ParsedLogBatchManager | None = None,
        recent_limit: int = 1000,
        on_log_parsed: Callable[[ParsedLog], None] | None = None,
        runtime_dependency_parser: RuntimeDependencyParser | None = None,
        on_trace_observation: Callable[[TraceObservation], None] | None = None,
        recent_trace_observation_limit: int = 1000,
        queue_drain_timeout_seconds: float = 30.0,
        benchmarking_collector: Any = None,
    ) -> None:
        """
        Initialize the Drain worker with dependencies and configuration.
        
        Args:
            log_buffer: The asynchronous queue providing raw ingest payloads.
            parser: The Drain3 parser instance.
            batch_manager: Manager for batching and persisting parsed logs.
            recent_limit: Number of recent parsed logs to keep in memory.
            on_log_parsed: Optional callback triggered when a log is successfully parsed.
            runtime_dependency_parser: Parser to extract topology traces from logs.
            on_trace_observation: Optional callback triggered when a trace is extracted.
            recent_trace_observation_limit: Number of recent traces to keep in memory.
            queue_drain_timeout_seconds: Maximum time to wait for the queue to drain during shutdown.
            benchmarking_collector: Optional collector for performance metrics.
            
        Raises:
            ValueError: If queue_drain_timeout_seconds is not positive.
        """
        if queue_drain_timeout_seconds <= 0:
            raise ValueError("queue_drain_timeout_seconds must be greater than 0")

        self.log_buffer: Any = log_buffer
        self.parser: DrainParser = parser
        self.batch_manager: ParsedLogBatchManager = batch_manager or ParsedLogBatchManager()
        self._recent_parsed_logs: deque[ParsedLog] = deque(maxlen=recent_limit)
        self._on_log_parsed: Callable[[ParsedLog], None] | None = on_log_parsed
        self.runtime_dependency_parser: RuntimeDependencyParser | None = runtime_dependency_parser
        self._recent_trace_observations: deque[TraceObservation] = deque(
            maxlen=recent_trace_observation_limit
        )
        self._on_trace_observation: Callable[[TraceObservation], None] | None = on_trace_observation
        self.queue_drain_timeout_seconds: float = queue_drain_timeout_seconds
        self.benchmarking_collector: Any = benchmarking_collector
        self.stream_name: str = "logs:stream"
        self.group_name: str = "log_workers"
        self._task: asyncio.Task[None] | None = None
        self._running: bool = False
        self.processed_count: int = 0
        self.error_count: int = 0
        self.last_processed_at: str | None = None
        self.last_queue_drain_timed_out: bool = False
        self.last_shutdown_batch_flush_failed: bool = False
        self.redis_client: Redis | None = None
        self.consumer_name: str = f"worker-{uuid.uuid4().hex[:8]}"
        self._recovery_task: asyncio.Task[None] | None = None
        self.recovery_idle_time_ms: int = 60000

    def set_redis_client(self, redis_client: Redis) -> None:
        """Set the Redis client for stream consumption."""
        self.redis_client = redis_client

    def start(self) -> None:
        """Start the background worker without blocking application startup."""
        if self._task and not self._task.done():
            return

        self._running = True
        self.batch_manager.start_periodic_flush()
        self._task = asyncio.create_task(self.run(), name="drain-worker")
        self._recovery_task = asyncio.create_task(self.recover_pending_messages(), name="drain-worker-recovery")

    async def stop(self) -> None:
        """Stop the consumer and flush parsed logs."""
        self.last_queue_drain_timed_out = False
        self.last_shutdown_batch_flush_failed = False
        self._running = False

        recovery_task = getattr(self, "_recovery_task", None)
        if recovery_task is not None:
            if not recovery_task.done():
                recovery_task.cancel()
            try:
                await recovery_task
            except asyncio.CancelledError:
                pass

        task = self._task
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
        """Continuously consume queued ingest payloads from Redis Streams."""
        if not self.redis_client:
            logger.error("Redis client not set for DrainWorker")
            return

        try:
            await self.redis_client.xgroup_create(self.stream_name, self.group_name, id="$", mkstream=True)
            logger.info("Redis consumer group '%s' initialized for %s", self.group_name, self.stream_name)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.exception("Failed to create consumer group")
                raise

        logger.info("Drain worker %s started consuming logs", self.consumer_name)

        while self._running:
            try:
                messages = await self.redis_client.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.stream_name: ">"},
                    count=500,
                    block=2000
                )
                
                if not messages:
                    continue

                for stream_name, stream_messages in messages:
                    for message_id, entry in stream_messages:
                        try:
                            # Extract payload
                            payload_json = entry.get(b"payload") or entry.get("payload")
                            if payload_json:
                                if isinstance(payload_json, bytes):
                                    payload_json = payload_json.decode("utf-8")
                                payload = json.loads(payload_json)
                                
                                # Process sequentially through Drain3, ML, DB, WebSockets
                                await self.process_one(payload)
                            
                            # Acknowledge on success
                            await self.redis_client.xack(self.stream_name, self.group_name, message_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            self.error_count += 1
                            logger.exception("Drain worker failed processing message %s", message_id)
                            
                # Trim the stream periodically to prevent unbounded growth
                try:
                    await self.redis_client.xtrim(self.stream_name, maxlen=100000, approximate=True)
                except Exception as e:
                    logger.warning("Failed to trim %s: %s", self.stream_name, str(e))
                            
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error_count += 1
                logger.exception("Drain worker XREADGROUP error")
                await asyncio.sleep(1)

    async def recover_pending_messages(self) -> None:
        """Background loop to auto-claim and process messages stuck in PEL."""
        if not getattr(self, "redis_client", None):
            return

        while self._running:
            try:
                await asyncio.sleep(30)
                if not self._running:
                    break

                min_idle_ms = getattr(self, "recovery_idle_time_ms", 60000)
                result = await self.redis_client.xautoclaim(
                    name=self.stream_name,
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    min_idle_time=min_idle_ms,
                    start_id="0-0",
                    count=100
                )
                
                if isinstance(result, tuple) or isinstance(result, list):
                    claimed_messages = result[1]
                    if claimed_messages:
                        logger.info("Auto-claimed %d pending messages from %s", len(claimed_messages), self.stream_name)
                        for message_id, entry in claimed_messages:
                            try:
                                payload_json = entry.get(b"payload") or entry.get("payload")
                                if payload_json:
                                    if isinstance(payload_json, bytes):
                                        payload_json = payload_json.decode("utf-8")
                                    payload = json.loads(payload_json)
                                    await self.process_one(payload)
                                
                                await self.redis_client.xack(self.stream_name, self.group_name, message_id)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                self.error_count += 1
                                logger.exception("Failed processing claimed message %s", message_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in recover_pending_messages: %s", str(e))

    async def process_one(self, item: Any) -> list[ParsedLog]:
        """Process one queued payload or log entry."""
        import time
        start_time = time.perf_counter()
        
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
                
            # Trigger base alert for errors (which will be deduplicated)
            if parsed.level.lower() == "error":
                payload = IncidentAlertPayload(
                    incident_id=parsed.id,
                    root_cause_service=parsed.service,
                    triggering_template=parsed.template_text or parsed.raw_message,
                    affected_services=[],
                    propagation_chain=[parsed.service],
                    confidence_score=0.5,
                    is_critical=False
                )
                asyncio.create_task(dispatch_incident_alert(payload, redis_client=self.redis_client))
            
            # Notify subscribers (e.g., feature extraction worker)
            if self._on_log_parsed:
                try:
                    self._on_log_parsed(parsed)
                except Exception:
                    logger.exception("Log parsed callback failed")

        if parsed_logs:
            event = telemetry_event("batch_processed", {
                "count": len(parsed_logs),
                "worker": self.consumer_name
            })
            asyncio.create_task(telemetry_manager.broadcast(event))

        if not parsed_logs and self.error_count == errors_before_extract:
            self.error_count += 1
            logger.warning("Drain worker could not extract any log messages from queued item: %r", item)

        if self.benchmarking_collector:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            self.benchmarking_collector.record_latency(duration_ms)

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
                "id": parsed.id,
                "source": parsed.source,
                "environment": parsed.environment,
                "service": parsed.service,
                "level": parsed.level,
                "template_id": parsed.template_id,
                "template": parsed.template_text,
                "correlation_id": parsed.correlation_id,
                "raw_message": getattr(parsed, "message", getattr(parsed, "raw", parsed.raw_message)),
                "metadata": parsed.metadata,
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

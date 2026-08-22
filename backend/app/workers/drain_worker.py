"""Async worker that drains queued ingest payloads into Drain3 parsing."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from drain3.redis_persistence import RedisPersistence
from redis.asyncio import Redis

from ..models import ParsedLog
from ..schemas.alerting import IncidentAlertPayload
from ..services.alerting import dispatch_incident_alert
from ..services.batch_manager import ParsedLogBatchManager
from ..services.drain_parser import (
    DrainParser,
    build_drain3_redis_persistence,
    get_drain3_state_backend,
)
from ..services.runtime_dependency_parser import (
    RuntimeDependencyParser,
    TraceObservation,
)
from ..services.telemetry import telemetry_event, telemetry_manager

logger = logging.getLogger("logsentinel.drain_worker")

DLQ_STREAM_NAME = "logs:dlq"
MAX_PARSE_RETRIES = 3


class StreamMessageOutcome(str, Enum):
    """Explicit terminal state for one Redis Stream delivery.

    ``XACK`` is permitted only for ``SUCCESSFULLY_PROCESSED`` or
    ``TERMINALLY_ROUTED_TO_DLQ``.  A retryable outcome deliberately leaves the
    delivery in the consumer group's pending entries list.
    """

    SUCCESSFULLY_PROCESSED = "successfully_processed"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINALLY_ROUTED_TO_DLQ = "terminally_routed_to_dlq"


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
        dlq_stream_name: str = DLQ_STREAM_NAME,
        max_retries: int = MAX_PARSE_RETRIES,
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
            dlq_stream_name: Redis stream name for dead-letter queue.
            max_retries: Consecutive failure threshold before routing to DLQ.
            
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
        self.dlq_stream_name: str = dlq_stream_name
        self.max_retries: int = max_retries
        self._retry_counts: dict[str, int] = {}
        self.dlq_count: int = 0
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
        
        parser_miner = getattr(self.parser, "_miner", None)
        current_persistence = getattr(parser_miner, "persistence_handler", None)
        self.redis_pers = (
            current_persistence
            if isinstance(current_persistence, RedisPersistence)
            else None
        )
        
        self._logs_since_snapshot = 0
        self._last_snapshot_time = time.monotonic()

    def set_redis_client(self, redis_client: Redis) -> None:
        """Set the Redis client for stream consumption."""
        self.redis_client = redis_client

        # If import-time Redis state loading fell back to the local file, try
        # one bounded hand-off after the application pool has proven Redis is
        # reachable. Custom parser test doubles keep their own handler.
        if (
            self.redis_pers is None
            and get_drain3_state_backend() == "redis"
            and isinstance(self.parser, DrainParser)
        ):
            previous = self.parser._miner.persistence_handler
            try:
                candidate = build_drain3_redis_persistence()
                self.parser._miner.persistence_handler = candidate
                self.parser._miner.load_state()
                self.redis_pers = candidate
            except Exception as exc:
                self.parser._miner.persistence_handler = previous
                logger.warning(
                    "Drain3 Redis state hand-off failed; retaining local state: %s",
                    exc,
                )

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

    async def _increment_retry_count(
        self, key: str, metadata: dict[str, Any] | None = None
    ) -> int:
        """Increment and return retry count for a log entry / message."""
        if metadata is not None and "_retry_count" in metadata:
            metadata["_retry_count"] = int(metadata["_retry_count"]) + 1
            return int(metadata["_retry_count"])

        if self.redis_client:
            redis_key = f"retry:drain:{key}"
            try:
                count = await self.redis_client.incr(redis_key)
                await self.redis_client.expire(redis_key, 86400)
                return int(count)
            except Exception:
                logger.debug(
                    "Redis retry counter unavailable for %s; using local fallback",
                    key,
                    exc_info=True,
                )

        count = self._retry_counts.get(key, 0) + 1
        self._retry_counts[key] = count
        return count

    async def _clear_retry_count(
        self, key: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Reset retry counter on success or terminal handling."""
        if metadata is not None and "_retry_count" in metadata:
            metadata.pop("_retry_count", None)

        if self.redis_client:
            redis_key = f"retry:drain:{key}"
            try:
                await self.redis_client.delete(redis_key)
            except Exception:
                logger.debug(
                    "Unable to clear Redis retry counter for %s",
                    key,
                    exc_info=True,
                )

        self._retry_counts.pop(key, None)

    async def _forward_to_dlq(
        self,
        raw_payload: str,
        error_traceback: str,
        log_id: str,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> str | None:
        """Forward a poisoned payload and error traceback to the dead-letter queue (logs:dlq)."""
        self.dlq_count += 1
        dlq_entry: dict[str, str] = {
            "payload": raw_payload,
            "error": error_traceback,
            "log_id": str(log_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            try:
                dlq_entry["metadata"] = json.dumps(metadata)
            except Exception:
                dlq_entry["metadata"] = str(metadata)
        if message_id:
            dlq_entry["stream_message_id"] = str(message_id)

        if self.redis_client:
            try:
                return await self.redis_client.xadd(self.dlq_stream_name, dlq_entry)
            except Exception:
                logger.exception("Failed to write poison pill to DLQ stream '%s'", self.dlq_stream_name)
        return None

    async def _ack_stream_message(self, message_id: str) -> bool:
        """ACK one delivery and report whether Redis accepted the operation."""
        if not self.redis_client:
            return False
        try:
            await self.redis_client.xack(self.stream_name, self.group_name, message_id)
            return True
        except Exception:
            logger.exception("Failed to XACK stream message %s", message_id)
            return False

    async def _retry_or_route_stream_failure(
        self,
        *,
        message_id: str,
        raw_payload: str,
        error_traceback: str,
        error_message: str,
        snippet: str,
    ) -> StreamMessageOutcome:
        """Keep a failed delivery pending or atomically route it to the DLQ.

        A failed DLQ write is itself retryable.  This prevents the worker from
        ACKing a message merely because it reached the retry threshold while
        the terminal sink was unavailable.
        """
        retry_key = f"msg:{message_id}"
        retry_count = await self._increment_retry_count(retry_key)
        if retry_count < self.max_retries:
            logger.error(
                "%s for message %s (attempt %d/%d). Payload snippet: %s",
                error_message,
                message_id,
                retry_count,
                self.max_retries,
                snippet,
                extra={
                    "message_id": message_id,
                    "payload_snippet": snippet,
                    "error": error_message,
                    "retry_count": retry_count,
                },
            )
            return StreamMessageOutcome.RETRYABLE_FAILURE

        dlq_id = await self._forward_to_dlq(
            raw_payload=raw_payload,
            error_traceback=error_traceback,
            log_id=f"msg-{message_id}",
            message_id=message_id,
        )
        if dlq_id is None:
            logger.error(
                "Failed to route message %s to DLQ after %d attempts; leaving it pending",
                message_id,
                retry_count,
            )
            return StreamMessageOutcome.RETRYABLE_FAILURE

        if not await self._ack_stream_message(message_id):
            # The DLQ copy is durable, but the original PEL entry remains
            # recoverable until XACK succeeds.  Do not claim terminal success.
            logger.error(
                "Message %s was written to DLQ %s but could not be ACKed; leaving it pending",
                message_id,
                dlq_id,
            )
            return StreamMessageOutcome.RETRYABLE_FAILURE

        await self._clear_retry_count(retry_key)
        logger.error(
            "Poison message %s (attempt %d) was terminally routed to DLQ '%s'. Payload snippet: %s",
            message_id,
            retry_count,
            self.dlq_stream_name,
            snippet,
            extra={
                "message_id": message_id,
                "payload_snippet": snippet,
                "error": error_message,
                "retry_count": retry_count,
                "dlq_stream": self.dlq_stream_name,
            },
        )
        return StreamMessageOutcome.TERMINALLY_ROUTED_TO_DLQ

    async def _process_stream_message(
        self,
        message_id: str,
        entry: dict[Any, Any],
    ) -> StreamMessageOutcome:
        """Process one Stream entry without ACKing retryable failures."""
        payload_raw = entry.get(b"payload") or entry.get("payload")
        if not payload_raw:
            self.error_count += 1
            return await self._retry_or_route_stream_failure(
                message_id=message_id,
                raw_payload="",
                error_traceback="Stream entry did not contain a payload field",
                error_message="Stream entry did not contain a payload",
                snippet="",
            )

        if isinstance(payload_raw, bytes):
            payload_str = payload_raw.decode("utf-8", errors="replace")
        else:
            payload_str = str(payload_raw)

        snippet = payload_str[:200]

        try:
            payload = json.loads(payload_str)
        except Exception as exc:
            self.error_count += 1
            return await self._retry_or_route_stream_failure(
                message_id=message_id,
                raw_payload=payload_str,
                error_traceback=traceback.format_exc(),
                error_message=f"JSON decode failed: {exc}",
                snippet=snippet,
            )

        try:
            parsed_logs = await self.process_one(
                payload,
                message_id=message_id,
                _raise_on_parser_error=True,
                _persist_before_ack=True,
            )
            if not parsed_logs:
                self.error_count += 1
                return await self._retry_or_route_stream_failure(
                    message_id=message_id,
                    raw_payload=payload_str,
                    error_traceback="No supported log entries were extracted from the stream payload",
                    error_message="No supported log entries were extracted",
                    snippet=snippet,
                )

            if not await self._ack_stream_message(message_id):
                return StreamMessageOutcome.RETRYABLE_FAILURE
            await self._clear_retry_count(f"msg:{message_id}")
            return StreamMessageOutcome.SUCCESSFULLY_PROCESSED
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error_count += 1
            return await self._retry_or_route_stream_failure(
                message_id=message_id,
                raw_payload=payload_str,
                error_traceback=traceback.format_exc(),
                error_message=f"Drain worker failed processing: {exc}",
                snippet=snippet,
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
                    block=2000,
                )

                if not messages:
                    continue

                for stream_name, stream_messages in messages:
                    for message_id, entry in stream_messages:
                        try:
                            await self._process_stream_message(message_id, entry)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            self.error_count += 1
                            logger.exception("Unexpected error processing stream message %s", message_id)

                # Trim the stream periodically to prevent unbounded growth
                try:
                    await self.redis_client.xtrim(self.stream_name, maxlen=500000, approximate=True)
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
                    count=100,
                )

                if isinstance(result, tuple) or isinstance(result, list):
                    claimed_messages = result[1]
                    if claimed_messages:
                        logger.info("Auto-claimed %d pending messages from %s", len(claimed_messages), self.stream_name)
                        for message_id, entry in claimed_messages:
                            try:
                                await self._process_stream_message(message_id, entry)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                self.error_count += 1
                                logger.exception("Failed processing claimed message %s", message_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in recover_pending_messages: %s", str(e))

    async def process_one(
        self,
        item: Any,
        message_id: str | None = None,
        *,
        _raise_on_parser_error: bool = False,
        _persist_before_ack: bool = False,
    ) -> list[ParsedLog]:
        """Process one queued payload or log entry."""
        import time
        start_time = time.perf_counter()

        baseline_flushed_records = 0
        if _persist_before_ack:
            baseline_stats = self.batch_manager.get_stats()
            baseline_flushed_records = int(baseline_stats.get("flushed_record_count", 0))

        parsed_logs: list[ParsedLog] = []
        errors_before_extract = self.error_count

        extracted_messages = self._extract_log_messages(item)
        for raw_message, metadata in extracted_messages:
            log_id = (
                metadata.get("id")
                or metadata.get("correlation_id")
                or (f"msg-{message_id}" if message_id else None)
                or f"raw-{hash(raw_message)}"
            )
            retry_key = str(log_id)
            snippet = raw_message[:200] if isinstance(raw_message, str) else str(raw_message)[:200]

            try:
                parsed = self.parser.parse(raw_message, metadata=metadata)
                await self._clear_retry_count(retry_key, metadata)
            except Exception as exc:
                self.error_count += 1
                if _raise_on_parser_error:
                    # Stream ownership handles retry counters and DLQ routing.
                    # Raising here prevents the outer loop from ACKing a parser
                    # failure as if the whole payload had succeeded.
                    raise
                tb_str = traceback.format_exc()
                retry_count = await self._increment_retry_count(retry_key, metadata)

                if retry_count >= self.max_retries:
                    await self._forward_to_dlq(
                        raw_payload=raw_message if isinstance(raw_message, str) else json.dumps(raw_message),
                        error_traceback=tb_str,
                        log_id=str(log_id),
                        metadata=metadata,
                        message_id=message_id,
                    )
                    await self._clear_retry_count(retry_key, metadata)

                    if message_id and self.redis_client:
                        try:
                            await self.redis_client.xack(self.stream_name, self.group_name, message_id)
                        except Exception:
                            logger.exception("Failed to XACK poisoned message %s from %s", message_id, self.stream_name)

                    logger.error(
                        "Poison pill detected for log ID %s (failed %d consecutive times). Routed to DLQ '%s'. Payload snippet: %s",
                        log_id,
                        retry_count,
                        self.dlq_stream_name,
                        snippet,
                        extra={
                            "log_id": str(log_id),
                            "payload_snippet": snippet,
                            "error": str(exc),
                            "traceback": tb_str,
                            "retry_count": retry_count,
                            "dlq_stream": self.dlq_stream_name,
                        },
                    )
                else:
                    logger.error(
                        "Drain parser failed for log ID %s (attempt %d/%d). Payload snippet: %s",
                        log_id,
                        retry_count,
                        self.max_retries,
                        snippet,
                        extra={
                            "log_id": str(log_id),
                            "payload_snippet": snippet,
                            "error": str(exc),
                            "traceback": tb_str,
                            "retry_count": retry_count,
                        },
                        exc_info=True,
                    )
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
                    is_critical=False,
                )
                asyncio.create_task(dispatch_incident_alert(payload, redis_client=self.redis_client))

            # Notify subscribers (e.g., feature extraction worker)
            if self._on_log_parsed:
                try:
                    self._on_log_parsed(parsed)
                except Exception:
                    logger.exception("Log parsed callback failed")

        if _persist_before_ack and parsed_logs:
            if not await self._flush_batch_before_stream_ack(
                parsed_count=len(parsed_logs),
                baseline_flushed_records=baseline_flushed_records,
            ):
                raise RuntimeError(
                    "Parsed log persistence did not complete; stream message remains retryable"
                )

        if parsed_logs:
            event = telemetry_event("batch_processed", {
                "count": len(parsed_logs),
                "worker": self.consumer_name,
            })
            asyncio.create_task(telemetry_manager.broadcast(event))

        if not parsed_logs and self.error_count == errors_before_extract:
            self.error_count += 1
            logger.warning("Drain worker could not extract any log messages from queued item: %r", item)

        if self.benchmarking_collector:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            self.benchmarking_collector.record_latency(duration_ms)

        self._logs_since_snapshot += len(parsed_logs)
        now = time.monotonic()
        if self._logs_since_snapshot >= 500 or (now - self._last_snapshot_time) >= 60.0:
            parser_miner = getattr(self.parser, "_miner", None)
            if self._logs_since_snapshot > 0 and parser_miner is not None:
                persistence_handler = getattr(parser_miner, "persistence_handler", None)
                try:
                    if self.redis_pers is not None:
                        parser_miner.persistence_handler = self.redis_pers
                    if parser_miner.persistence_handler is not None:
                        parser_miner.save_state("periodic")
                except Exception as e:
                    logger.error("Failed to save Drain3 snapshot: %s", e)
                finally:
                    parser_miner.persistence_handler = persistence_handler
            self._logs_since_snapshot = 0
            self._last_snapshot_time = now

        return parsed_logs

    async def _flush_batch_before_stream_ack(
        self,
        *,
        parsed_count: int,
        baseline_flushed_records: int,
    ) -> bool:
        """Ensure parsed records reached the configured batch sink.

        ``ParsedLogBatchManager.add`` intentionally buffers below its normal
        threshold. A Stream delivery cannot be ACKed while those records are
        only in memory, so the Stream path explicitly flushes and verifies the
        manager's durable-success counters before returning success.
        """
        await self.batch_manager.flush()
        stats = self.batch_manager.get_stats()
        pending_records = int(stats.get("current_buffer_size", 0))
        flushed_records = int(stats.get("flushed_record_count", 0))
        sink_error = stats.get("last_sink_error")

        if pending_records > 0 or sink_error:
            logger.error(
                "Parsed log persistence is not durable yet; pending_records=%d error=%s",
                pending_records,
                sink_error,
            )
            return False

        if flushed_records < baseline_flushed_records + parsed_count:
            logger.error(
                "Parsed log persistence counter did not advance for stream delivery: "
                "before=%d after=%d expected_at_least=%d",
                baseline_flushed_records,
                flushed_records,
                baseline_flushed_records + parsed_count,
            )
            return False

        return True

    def get_stats(self) -> dict[str, Any]:
        """Return worker counters and queue visibility."""
        return {
            "running": self._running,
            "processed_count": self.processed_count,
            "error_count": self.error_count,
            "dlq_count": self.dlq_count,
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

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
from redis.typing import EncodableT

from ..core.constants import LOG_STREAM_NAME, LOG_WORKERS_GROUP
from ..core.pipeline_identity import logical_telemetry_id
from ..models import ParsedLog
from ..repositories.log_repository import (
    PersistResult,
    PersistResults,
    PersistStatus,
)
from ..schemas.alerting import IncidentAlertPayload
from ..schemas.stream import StreamEnvelope
from ..security.redaction import redact_text, redact_value
from ..security.tenant_boundary import (
    assert_tenant_identity,
    reject_untrusted_tenant_fields,
)
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

DLQ_STREAM_NAME = f"{LOG_STREAM_NAME}:dlq"
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
        self.batch_manager: ParsedLogBatchManager = (
            batch_manager or ParsedLogBatchManager()
        )
        self._recent_parsed_logs: deque[ParsedLog] = deque(maxlen=recent_limit)
        self._on_log_parsed: Callable[[ParsedLog], None] | None = on_log_parsed
        self.runtime_dependency_parser: RuntimeDependencyParser | None = (
            runtime_dependency_parser
        )
        self._recent_trace_observations: deque[TraceObservation] = deque(
            maxlen=recent_trace_observation_limit
        )
        self._on_trace_observation: Callable[[TraceObservation], None] | None = (
            on_trace_observation
        )
        self.queue_drain_timeout_seconds: float = queue_drain_timeout_seconds
        self.benchmarking_collector: Any = benchmarking_collector
        self.stream_name: str = LOG_STREAM_NAME
        self.group_name: str = LOG_WORKERS_GROUP
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
                    redact_text(str(exc)),
                )

    def start(self) -> None:
        """Start the background worker without blocking application startup."""
        if self._task and not self._task.done():
            return

        self._running = True
        self.batch_manager.start_periodic_flush()
        self._task = asyncio.create_task(self.run(), name="drain-worker")
        self._recovery_task = asyncio.create_task(
            self.recover_pending_messages(), name="drain-worker-recovery"
        )

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
            except Exception as exc:
                logger.debug(
                    "Redis retry counter unavailable for %s; using local fallback exception_type=%s detail=%s",
                    key,
                    type(exc).__name__,
                    redact_text(str(exc)),
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
            except Exception as exc:
                logger.debug(
                    "Unable to clear Redis retry counter for %s exception_type=%s detail=%s",
                    key,
                    type(exc).__name__,
                    redact_text(str(exc)),
                )

        self._retry_counts.pop(key, None)

    async def _forward_to_dlq(
        self,
        raw_payload: str,
        error_traceback: str,
        log_id: str,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
        trusted_tenant_id: str | None = None,
    ) -> str | None:
        """Forward a poisoned payload and error traceback to the dead-letter queue (logs:dlq)."""
        self.dlq_count += 1
        tenant_id = str(trusted_tenant_id or "").strip()
        dlq_stream = (
            f"{self.dlq_stream_name}:{tenant_id}" if tenant_id else self.dlq_stream_name
        )
        dlq_entry: dict[EncodableT, EncodableT] = {
            "payload": redact_text(raw_payload),
            "error": redact_text(error_traceback),
            "log_id": str(log_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            try:
                safe_metadata = dict(metadata)
                safe_metadata.pop("tenant_id", None)
                dlq_entry["metadata"] = json.dumps(redact_value(safe_metadata))
            except Exception:
                dlq_entry["metadata"] = redact_text(str(metadata))
        if message_id:
            dlq_entry["stream_message_id"] = str(message_id)

        if self.redis_client:
            try:
                stream_message_id = await self.redis_client.xadd(
                    dlq_stream, dlq_entry, maxlen=10000, approximate=True
                )
                expire = getattr(self.redis_client, "expire", None)
                if callable(expire):
                    await expire(dlq_stream, 7 * 24 * 60 * 60)
                return (
                    stream_message_id.decode()
                    if isinstance(stream_message_id, bytes)
                    else stream_message_id
                )
            except Exception as exc:
                logger.error(
                    "Failed to write poison pill to DLQ stream '%s' exception_type=%s detail=%s",
                    self.dlq_stream_name,
                    type(exc).__name__,
                    redact_text(str(exc)),
                )
        return None

    async def _ack_stream_message(self, message_id: str) -> bool:
        """ACK one delivery and report whether Redis accepted the operation."""
        if not self.redis_client:
            return False
        try:
            await self.redis_client.xack(self.stream_name, self.group_name, message_id)
            return True
        except Exception as exc:
            logger.error(
                "Failed to XACK stream message %s exception_type=%s detail=%s",
                message_id,
                type(exc).__name__,
                redact_text(str(exc)),
            )
            return False

    async def _retry_or_route_stream_failure(
        self,
        *,
        message_id: str,
        raw_payload: str,
        error_traceback: str,
        error_message: str,
        metadata: dict[str, Any] | None = None,
        trusted_tenant_id: str | None = None,
    ) -> StreamMessageOutcome:
        """Keep a failed delivery pending or atomically route it to the DLQ.

        A failed DLQ write is itself retryable.  This prevents the worker from
        ACKing a message merely because it reached the retry threshold while
        the terminal sink was unavailable.
        """
        safe_error_message = redact_text(error_message)
        safe_error_traceback = redact_text(error_traceback)
        retry_key = f"msg:{message_id}"
        retry_count = await self._increment_retry_count(retry_key)
        if retry_count < self.max_retries:
            logger.error(
                "%s for message %s (attempt %d/%d)",
                safe_error_message,
                message_id,
                retry_count,
                self.max_retries,
                extra={
                    "message_id": message_id,
                    "payload_redacted": True,
                    "error": safe_error_message,
                    "retry_count": retry_count,
                },
            )
            return StreamMessageOutcome.RETRYABLE_FAILURE

        dlq_id = await self._forward_to_dlq(
            raw_payload=raw_payload,
            error_traceback=safe_error_traceback,
            log_id=f"msg-{message_id}",
            metadata=metadata,
            message_id=message_id,
            trusted_tenant_id=trusted_tenant_id,
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
            "Poison message %s (attempt %d) was terminally routed to DLQ '%s'",
            message_id,
            retry_count,
            self.dlq_stream_name,
            extra={
                "message_id": message_id,
                "payload_redacted": True,
                "error": safe_error_message,
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
            )

        if isinstance(payload_raw, bytes):
            payload_str = payload_raw.decode("utf-8", errors="replace")
        else:
            payload_str = str(payload_raw)

        try:
            payload = json.loads(payload_str)
        except Exception as exc:
            self.error_count += 1
            return await self._retry_or_route_stream_failure(
                message_id=message_id,
                raw_payload=payload_str,
                error_traceback=redact_text(traceback.format_exc()),
                error_message=f"JSON decode failed: {redact_text(str(exc))}",
            )

        envelope: StreamEnvelope | None = None
        try:
            envelope = StreamEnvelope.model_validate(payload)
        except Exception as exc:
            # Legacy unwrapped stream records are accepted only by old unit
            # tests. A real worker must fail closed because such records have
            # no authenticated tenant authority.
            import os

            if not (
                os.getenv("ENVIRONMENT", "").strip().lower() == "test"
                or os.getenv("PYTEST_CURRENT_TEST")
            ):
                self.error_count += 1
                return await self._retry_or_route_stream_failure(
                    message_id=message_id,
                    raw_payload=payload_str,
                    error_traceback=redact_text(traceback.format_exc()),
                    error_message=f"Invalid trusted stream envelope: {type(exc).__name__}",
                )

        try:
            parsed_logs = await self.process_one(
                envelope.payload if envelope is not None else payload,
                message_id=message_id,
                _raise_on_parser_error=True,
                _persist_before_ack=True,
                trusted_tenant_id=envelope.tenant_id if envelope is not None else None,
                trusted_owner_user_id=envelope.owner_user_id
                if envelope is not None
                else None,
            )
            if not parsed_logs:
                self.error_count += 1
                return await self._retry_or_route_stream_failure(
                    message_id=message_id,
                    raw_payload=payload_str,
                    error_traceback="No supported log entries were extracted from the stream payload",
                    error_message="No supported log entries were extracted",
                    metadata=(
                        envelope.payload
                        if envelope and isinstance(envelope.payload, dict)
                        else None
                    ),
                    trusted_tenant_id=envelope.tenant_id
                    if envelope is not None
                    else None,
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
                error_traceback=redact_text(traceback.format_exc()),
                error_message=f"Drain worker failed processing: {redact_text(str(exc))}",
                metadata=(
                    envelope.payload
                    if envelope and isinstance(envelope.payload, dict)
                    else None
                ),
                trusted_tenant_id=envelope.tenant_id if envelope is not None else None,
            )

    async def run(self) -> None:
        """Continuously consume queued ingest payloads from Redis Streams."""
        if not self.redis_client:
            logger.error("Redis client not set for DrainWorker")
            return

        try:
            await self.redis_client.xgroup_create(
                self.stream_name, self.group_name, id="$", mkstream=True
            )
            logger.info(
                "Redis consumer group '%s' initialized for %s",
                self.group_name,
                self.stream_name,
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.error(
                    "Failed to create consumer group exception_type=%s detail=%s",
                    type(e).__name__,
                    redact_text(str(e)),
                )
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

                for stream_name, stream_messages in messages:  # type: ignore
                    for message_id, entry in stream_messages:  # type: ignore
                        try:
                            await self._process_stream_message(message_id, entry)  # type: ignore
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            self.error_count += 1
                            logger.error(
                                "Unexpected error processing stream message %s exception_type=%s detail=%s",
                                message_id,
                                type(exc).__name__,
                                redact_text(str(exc)),
                            )

                # Trim the stream periodically to prevent unbounded growth
                try:
                    await self.redis_client.xtrim(
                        self.stream_name, maxlen=500000, approximate=True
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to trim %s exception_type=%s detail=%s",
                        self.stream_name,
                        type(e).__name__,
                        redact_text(str(e)),
                    )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error_count += 1
                logger.error(
                    "Drain worker XREADGROUP error exception_type=%s detail=%s",
                    type(exc).__name__,
                    redact_text(str(exc)),
                )
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
                result = await self.redis_client.xautoclaim(  # type: ignore
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
                        logger.info(
                            "Auto-claimed %d pending messages from %s",
                            len(claimed_messages),
                            self.stream_name,
                        )
                        for message_id, entry in claimed_messages:
                            try:
                                await self._process_stream_message(message_id, entry)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                self.error_count += 1
                                logger.error(
                                    "Failed processing claimed message %s exception_type=%s detail=%s",
                                    message_id,
                                    type(exc).__name__,
                                    redact_text(str(exc)),
                                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Error in recover_pending_messages exception_type=%s detail=%s",
                    type(e).__name__,
                    redact_text(str(e)),
                )

    async def process_one(
        self,
        item: Any,
        message_id: str | None = None,
        *,
        _raise_on_parser_error: bool = False,
        _persist_before_ack: bool = False,
        trusted_tenant_id: str | None = None,
        trusted_owner_user_id: int | None = None,
    ) -> list[ParsedLog]:
        """Process one queued payload or log entry."""
        start_time = time.perf_counter()

        parsed_logs: list[ParsedLog] = []
        downstream_logs: list[ParsedLog] = []
        deferred_traces: list[tuple[ParsedLog, Any]] = []
        errors_before_extract = self.error_count

        extracted_messages = self._extract_log_messages(
            item, trusted_tenant_id=trusted_tenant_id
        )
        for raw_message, metadata in extracted_messages:
            log_id = (
                metadata.get("id")
                or metadata.get("correlation_id")
                or (f"msg-{message_id}" if message_id else None)
                or f"raw-{hash(raw_message)}"
            )
            retry_key = str(log_id)
            try:
                if trusted_tenant_id is None:
                    parsed = self.parser.parse(raw_message, metadata=metadata)
                else:
                    parsed = self.parser.parse(
                        raw_message,
                        metadata=metadata,
                        trusted_tenant_id=trusted_tenant_id,
                        trusted_owner_user_id=trusted_owner_user_id,
                    )
                if trusted_tenant_id is not None:
                    assert_tenant_identity(
                        trusted_tenant_id,
                        parsed.tenant_id,
                        boundary="parsed-log",
                    )
                await self._clear_retry_count(retry_key, metadata)
            except Exception as exc:
                self.error_count += 1
                if _raise_on_parser_error:
                    # Stream ownership handles retry counters and DLQ routing.
                    # Raising here prevents the outer loop from ACKing a parser
                    # failure as if the whole payload had succeeded.
                    raise
                tb_str = redact_text(traceback.format_exc())
                retry_count = await self._increment_retry_count(retry_key, metadata)

                if retry_count >= self.max_retries:
                    await self._forward_to_dlq(
                        raw_payload=raw_message
                        if isinstance(raw_message, str)
                        else json.dumps(raw_message),
                        error_traceback=tb_str,
                        log_id=str(log_id),
                        metadata=metadata,
                        message_id=message_id,
                        trusted_tenant_id=trusted_tenant_id,
                    )
                    await self._clear_retry_count(retry_key, metadata)

                    if message_id and self.redis_client:
                        try:
                            await self.redis_client.xack(
                                self.stream_name, self.group_name, message_id
                            )
                        except Exception as ack_exc:
                            logger.error(
                                "Failed to XACK poisoned message %s from %s exception_type=%s detail=%s",
                                message_id,
                                self.stream_name,
                                type(ack_exc).__name__,
                                redact_text(str(ack_exc)),
                            )

                    logger.error(
                        "Poison pill detected for log ID %s (failed %d consecutive times). Routed to DLQ '%s'",
                        log_id,
                        retry_count,
                        self.dlq_stream_name,
                        extra={
                            "log_id": str(log_id),
                            "payload_redacted": True,
                            "error": redact_text(str(exc)),
                            "error_type": type(exc).__name__,
                            "retry_count": retry_count,
                            "dlq_stream": self.dlq_stream_name,
                        },
                    )
                else:
                    logger.error(
                        "Drain parser failed for log ID %s (attempt %d/%d)",
                        log_id,
                        retry_count,
                        self.max_retries,
                        extra={
                            "log_id": str(log_id),
                            "payload_redacted": True,
                            "error": redact_text(str(exc)),
                            "error_type": type(exc).__name__,
                            "retry_count": retry_count,
                        },
                    )
                continue

            trace_observation = self._extract_trace_observation(parsed)
            if _persist_before_ack:
                # Stream deliveries use a private batch so no downstream
                # callback can run until PostgreSQL returns typed acceptance.
                parsed_logs.append(parsed)
                deferred_traces.append((parsed, trace_observation))
                continue

            await self._activate_parsed_log(
                parsed,
                trace_observation,
                trusted_tenant_id=trusted_tenant_id,
                schedule_error_alert=True,
                add_to_batch=True,
            )
            parsed_logs.append(parsed)
            downstream_logs.append(parsed)

        if _persist_before_ack and parsed_logs:
            persistence_results = await self._persist_stream_batch(parsed_logs)
            if len(persistence_results) != len(parsed_logs):
                raise RuntimeError(
                    "raw persistence returned incomplete per-event results"
                )
            for parsed, result, (_, trace_observation) in zip(
                parsed_logs, persistence_results, deferred_traces
            ):
                if result.status == PersistStatus.FAILED:
                    raise RuntimeError("raw persistence failed for stream event")
                if result.status == PersistStatus.NEWLY_INSERTED:
                    await self._activate_parsed_log(
                        parsed,
                        trace_observation,
                        trusted_tenant_id=trusted_tenant_id,
                        schedule_error_alert=False,
                        add_to_batch=False,
                    )
                    downstream_logs.append(parsed)

        if downstream_logs:
            for tenant_id, owner_user_id in sorted(
                {(log.tenant_id, log.owner_user_id) for log in downstream_logs}
            ):
                event = telemetry_event(
                    "batch_processed",
                    {
                        "count": sum(
                            log.tenant_id == tenant_id
                            and log.owner_user_id == owner_user_id
                            for log in downstream_logs
                        ),
                        "worker": self.consumer_name,
                    },
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                    logical_id="|".join(
                        sorted(
                            str(log.event_id or log.id)
                            for log in downstream_logs
                            if log.tenant_id == tenant_id
                            and log.owner_user_id == owner_user_id
                        )
                    ),
                )
                asyncio.create_task(telemetry_manager.broadcast(event))

        if not parsed_logs and self.error_count == errors_before_extract:
            self.error_count += 1
            logger.warning(
                "Drain worker could not extract any log messages from queued item",
                extra={"payload_redacted": True},
            )

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
                    logger.error(
                        "Failed to save Drain3 snapshot: %s", redact_text(str(e))
                    )
                finally:
                    parser_miner.persistence_handler = persistence_handler
            self._logs_since_snapshot = 0
            self._last_snapshot_time = now

        return parsed_logs

    async def _persist_stream_batch(
        self, parsed_logs: list[ParsedLog]
    ) -> PersistResults:
        """Persist raw rows and return one explicit result per source event."""
        success, result = await self.batch_manager.persist_batch(parsed_logs)
        if not success:
            raise RuntimeError(
                "Parsed log persistence or downstream registration failed; "
                "stream message remains retryable"
            )
        if isinstance(result, PersistResults):
            return result
        # Compatibility sinks used by older unit tests returned an integer or
        # a simple success marker. Such a sink cannot report replays, so its
        # result is conservatively treated as newly accepted for this delivery.
        return PersistResults(
            PersistResult(
                tenant_id=parsed.tenant_id,
                event_id=str(parsed.event_id or parsed.id),
                status=PersistStatus.NEWLY_INSERTED,
            )
            for parsed in parsed_logs
        )

    async def _activate_parsed_log(
        self,
        parsed: ParsedLog,
        trace_observation: Any,
        *,
        trusted_tenant_id: str | None,
        schedule_error_alert: bool,
        add_to_batch: bool,
    ) -> None:
        """Run downstream callbacks only after raw acceptance is established."""
        self._recent_parsed_logs.append(parsed)
        if add_to_batch:
            await self.batch_manager.add(parsed)
        self.processed_count += 1
        self.last_processed_at = datetime.now(timezone.utc).isoformat()
        self._schedule_log_parsed_event(parsed)
        if trace_observation is not None:
            self._record_trace_observation(trace_observation)

        if schedule_error_alert and parsed.level.lower() == "error":
            payload = IncidentAlertPayload(
                tenant_id=parsed.tenant_id,
                owner_user_id=parsed.owner_user_id,
                incident_id=parsed.event_id or parsed.id,
                root_cause_service=parsed.service,
                triggering_template=parsed.template_text or parsed.raw_message,
                affected_services=[],
                propagation_chain=[parsed.service],
                confidence_score=0.5,
                is_critical=False,
            )
            if trusted_tenant_id is not None:
                assert_tenant_identity(
                    trusted_tenant_id,
                    payload.tenant_id,
                    boundary="incident-alert",
                )
            await dispatch_incident_alert(payload, redis_client=self.redis_client)

        if self._on_log_parsed:
            try:
                self._on_log_parsed(parsed)
            except Exception as exc:
                logger.error(
                    "Log parsed callback failed after durable acceptance exception_type=%s detail=%s",
                    type(exc).__name__,
                    redact_text(str(exc)),
                )

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
                redact_text(str(sink_error)),
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
        recent = (
            list(self._recent_parsed_logs)[-safe_limit:][::-1] if safe_limit else []
        )
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

    def _extract_log_messages(
        self,
        item: Any,
        *,
        trusted_tenant_id: str | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        if isinstance(item, str):
            return [(item, {})]

        if not isinstance(item, dict):
            return []

        if trusted_tenant_id is not None:
            reject_untrusted_tenant_fields(item, path="stream_payload")
        parent_metadata = self._metadata_from_payload(
            item, trusted_tenant_id=trusted_tenant_id
        )
        logs = item.get("logs")

        if isinstance(logs, list):
            extracted: list[tuple[str, dict[str, Any]]] = []
            for entry in logs:
                entry_messages = self._extract_entry(
                    entry,
                    parent_metadata,
                    trusted_tenant_id=trusted_tenant_id,
                )
                if not entry_messages:
                    self._record_unsupported(entry)
                extracted.extend(entry_messages)
            return extracted

        entry_messages = self._extract_entry(
            item,
            parent_metadata,
            trusted_tenant_id=trusted_tenant_id,
        )
        if not entry_messages:
            self._record_unsupported(item)
        return entry_messages

    def _extract_entry(
        self,
        entry: Any,
        parent_metadata: dict[str, Any],
        *,
        trusted_tenant_id: str | None = None,
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
            if trusted_tenant_id is not None:
                reject_untrusted_tenant_fields(nested_metadata, path="event_metadata")
            metadata.update(nested_metadata)

        for source_key, target_key in (
            ("service_name", "service"),
            ("service", "service"),
            ("level", "level"),
            ("timestamp", "timestamp"),
            ("correlation_id", "correlation_id"),
            ("event_id", "event_id"),
        ):
            value = entry.get(source_key)
            if value is not None:
                metadata[target_key] = value

        return [(raw_message, metadata)]

    def _metadata_from_payload(
        self,
        item: dict[str, Any],
        *,
        trusted_tenant_id: str | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in ("source", "environment", "correlation_id"):
            value = item.get(key)
            if value is not None:
                metadata[key] = value
        if trusted_tenant_id is None and item.get("tenant_id") is not None:
            # Compatibility for direct legacy helper callers. Stream
            # messages always provide trusted_tenant_id and never use this.
            metadata["tenant_id"] = item["tenant_id"]
        return metadata

    def _find_message(self, entry: dict[str, Any]) -> str | None:
        for key in ("raw_message", "raw", "message", "log"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _record_unsupported(self, item: Any) -> None:
        self.error_count += 1
        logger.warning(
            "Drain worker found unsupported log entry shape: %s",
            redact_text(str(redact_value(item))),
        )

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
            },
            tenant_id=parsed.tenant_id,
            owner_user_id=parsed.owner_user_id,
            logical_id=logical_telemetry_id(
                "raw-log-parsed",
                parsed.tenant_id,
                str(parsed.event_id or parsed.id),
                owner_user_id=parsed.owner_user_id,
            ),
        )

        try:
            asyncio.create_task(telemetry_manager.broadcast(event))
        except RuntimeError:
            logger.debug(
                "No running event loop available for log.parsed telemetry broadcast"
            )

    def _extract_trace_observation(self, parsed: ParsedLog) -> TraceObservation | None:
        if self.runtime_dependency_parser is None:
            return None
        try:
            return self.runtime_dependency_parser.extract(parsed)
        except Exception as exc:
            logger.error(
                "Runtime dependency trace extraction failed exception_type=%s detail=%s",
                type(exc).__name__,
                redact_text(str(exc)),
            )
            return None

    def _record_trace_observation(self, observation: TraceObservation) -> None:
        self._recent_trace_observations.append(observation)
        if self._on_trace_observation is None:
            return
        try:
            self._on_trace_observation(observation)
        except Exception as exc:
            logger.error(
                "Trace observation callback failed exception_type=%s detail=%s",
                type(exc).__name__,
                redact_text(str(exc)),
            )

"""Async worker for managing tracking infrastructure loops based on anomaly scores.

This worker receives anomaly scores from the machine learning pipeline and
triggers automated tracking and alert loops when thresholds are met.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from ..core.settings import GraphScoringSettings, get_graph_scoring_settings
from ..ml.anomaly_scoring import normalize_prediction_anomaly_score
from ..models import FeatureVector, PerformanceEvent
from ..observability.metrics import EVENT_QUEUE_DROPS_TOTAL
from ..repositories.tracking_repository import TrackingRepository
from ..schemas.alerting import IncidentAlertPayload
from ..schemas.blast_radius import BlastRadiusResult
from ..security.redaction import sanitize_error_text
from ..services.benchmarking import BenchmarkingCollector
from ..services.graph_analysis_service import GraphAnalysisService
from ..services.telemetry import telemetry_event, telemetry_manager

logger = logging.getLogger("logsentinel.event_manager")


class EventManager:
    """Background worker that evaluates anomaly scores and automates tracking loops."""

    def __init__(
        self,
        tracking_repository: TrackingRepository | None = None,
        graph_analysis_service: GraphAnalysisService | None = None,
        graph_scoring_settings: GraphScoringSettings | None = None,
        telemetry_broadcaster: Any | None = None,
        benchmarking_collector: BenchmarkingCollector | None = None,
        max_queue_size: int = 10000,
    ) -> None:
        """Initialize the event manager.

        Args:
            tracking_repository: Repository to persist tracking loops.
            max_queue_size: Maximum size of the incoming queue.
        """
        self.tracking_repository = tracking_repository or TrackingRepository()
        self.graph_analysis_service = graph_analysis_service
        self.graph_scoring_settings = (
            graph_scoring_settings or get_graph_scoring_settings()
        )
        self.telemetry_broadcaster = telemetry_broadcaster or telemetry_manager
        self.benchmarking_collector = benchmarking_collector
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max_queue_size)

        if self.benchmarking_collector:
            self.benchmarking_collector.bind_event_manager(self)

        self._task: asyncio.Task[None] | None = None
        self._running = False
        self.redis_client = None
        self._processed_count = 0
        self._error_count = 0
        self._noncritical_queue_drops = 0
        self._critical_queue_rejections = 0
        self._last_processed_at: str | None = None

        logger.info("EventManager initialized")

    def set_redis_client(self, redis_client) -> None:
        """Inject the initialized application Redis client for cooldowns."""
        self.redis_client = redis_client

    def start(self) -> None:
        """Start the background event manager loop."""
        if self._task and not self._task.done():
            logger.warning("EventManager already running")
            return

        self._running = True
        self._task = asyncio.create_task(self.run(), name="event-manager-worker")
        logger.info("EventManager started")

    async def stop(self) -> None:
        """Stop the background worker cleanly."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

        logger.info("EventManager stopped")

    def enqueue_feature_vector(self, feature_vector: FeatureVector) -> bool:
        """Reject the obsolete in-memory critical path explicitly.

        Feature vectors are accepted by the durable ``feature_window`` outbox
        in ``FeatureExtractionWorker``. Keeping this compatibility method from
        silently dropping a critical event is safer than treating the bounded
        queue as an acceptance boundary.
        """
        del feature_vector
        self._critical_queue_rejections += 1
        logger.error(
            "Critical feature event rejected from the in-memory queue; "
            "the durable feature_window outbox is the only accepted path"
        )
        raise RuntimeError(
            "critical feature events must be registered in the durable feature stage"
        )

    def enqueue_performance_event(self, event: PerformanceEvent) -> bool:
        """Enqueue a performance event for alerting without blocking."""
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self._noncritical_queue_drops += 1
            EVENT_QUEUE_DROPS_TOTAL.labels(event_class="noncritical").inc()
            logger.warning(
                "EventManager queue is full; dropping explicitly noncritical performance event"
            )
            return False

    async def run(self) -> None:
        """Main worker loop that dequeues and evaluates events."""
        while self._running:
            event = None
            try:
                event = await self.queue.get()
                if isinstance(event, FeatureVector):
                    await self._process_event(event)
                elif isinstance(event, PerformanceEvent):
                    await self._process_performance_event(event)
                self._processed_count += 1
                self._last_processed_at = datetime.now(timezone.utc).isoformat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._error_count += 1
                logger.error(
                    "EventManager encountered an error processing an event exception_type=%s detail=%s",
                    type(exc).__name__,
                    sanitize_error_text(exc),
                )
            finally:
                if event is not None:
                    self.queue.task_done()

    def get_stats(self) -> dict[str, Any]:
        """Return bounded worker state for readiness and Prometheus sampling."""
        return {
            "running": self._running,
            "processed_count": self._processed_count,
            "error_count": self._error_count,
            # This queue has no durable DLQ. Do not alias processing errors to
            # a durable failed-row count.
            "dlq_count": 0,
            "noncritical_queue_drops": self._noncritical_queue_drops,
            "critical_queue_rejections": self._critical_queue_rejections,
            "queue_size": self.queue.qsize(),
            "last_processed_at": self._last_processed_at,
        }

    async def _process_performance_event(self, event: PerformanceEvent) -> None:
        """Process and broadcast a performance threshold breach alert."""
        try:
            payload = event.model_dump(mode="json")
            await self.telemetry_broadcaster.broadcast(
                telemetry_event(
                    "system.performance.alert",
                    payload,
                )
            )
            logger.warning(
                f"Performance alert triggered: {event.metric_name} = {event.current_value} (threshold {event.threshold})"
            )
        except Exception as exc:
            logger.error(
                "Failed to broadcast performance event exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )

    async def process_feature_vector(
        self,
        feature_vector: FeatureVector,
        *,
        connection: Any | None = None,
        broadcast: bool = True,
    ) -> None:
        """Process feature work from the durable stage, bypassing EventManager.queue."""
        await self._process_event(
            feature_vector, connection=connection, broadcast=broadcast
        )

    async def _process_event(
        self,
        feature_vector: FeatureVector,
        *,
        connection: Any | None = None,
        broadcast: bool = True,
    ) -> None:
        """Evaluate a single feature vector and trigger tracking loops if needed."""
        prediction = feature_vector.anomaly_prediction
        if not isinstance(prediction, dict):
            return

        is_anomaly = prediction.get("is_anomaly")
        anomaly_score = normalize_prediction_anomaly_score(prediction)

        if is_anomaly is True:
            logger.info(
                "Anomaly detected (score %.3f). Triggering tracking loop for window_id=%s",
                anomaly_score,
                feature_vector.window_id,
            )
            await self._trigger_tracking_loop(
                feature_vector,
                anomaly_score,
                prediction,
                connection=connection,
                broadcast=broadcast,
            )

    async def _trigger_tracking_loop(
        self,
        feature_vector: FeatureVector,
        anomaly_score: float,
        prediction: dict[str, Any],
        *,
        connection: Any | None = None,
        broadcast: bool = True,
    ) -> None:
        """Create a tracking loop in the database and emit an alert telemetry event."""
        blast_radius_result = await self._run_graph_analysis(feature_vector)
        blast_radius_payload = (
            blast_radius_result.model_dump(mode="json")
            if blast_radius_result is not None
            else None
        )

        service_dist = feature_vector.service_distribution
        dominant_service = (
            max(service_dist.items(), key=lambda x: x[1])[0]
            if service_dist
            else "unknown"
        )
        alert_payload = IncidentAlertPayload(
            tenant_id=feature_vector.tenant_id,
            owner_user_id=feature_vector.owner_user_id,
            incident_id=feature_vector.window_id,
            root_cause_service=dominant_service,
            triggering_template=None,
            affected_services=[dominant_service],
            confidence_score=anomaly_score,
            is_critical=(anomaly_score >= 0.7),
        )

        # Persist to database
        try:
            persist_on_connection = getattr(
                self.tracking_repository, "persist_tracking_loop_on_connection", None
            )
            if connection is not None and callable(persist_on_connection):
                created = await persist_on_connection(
                    connection,
                    tenant_id=feature_vector.tenant_id,
                    owner_user_id=feature_vector.owner_user_id,
                    window_id=feature_vector.window_id,
                    anomaly_score=anomaly_score,
                    status="ACTIVE",
                    blast_radius=blast_radius_payload,
                    alert_payload=alert_payload,
                )
            else:
                created = await self.tracking_repository.persist_tracking_loop(  # type: ignore
                    tenant_id=feature_vector.tenant_id,
                    owner_user_id=feature_vector.owner_user_id,
                    window_id=feature_vector.window_id,
                    anomaly_score=anomaly_score,
                    status="ACTIVE",
                    blast_radius=blast_radius_payload,
                    alert_payload=alert_payload,
                )
        except Exception as exc:
            logger.error(
                "Failed to persist tracking loop in EventManager exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )
            raise

        # WebSocket telemetry is a best-effort UI hint. A durable feature retry
        # must not replay it, and a crash before this point must not affect the
        # committed feature/anomaly/incident truth.
        if not broadcast or created is False:
            return
        try:
            # Derive severity from prediction or fall back to score-based classification
            severity = prediction.get("severity")
            if not severity or not isinstance(severity, str):
                if anomaly_score >= 0.9:
                    severity = "critical"
                elif anomaly_score >= 0.7:
                    severity = "high"
                elif anomaly_score >= 0.5:
                    severity = "medium"
                else:
                    severity = "low"

            payload = {
                "window_id": feature_vector.window_id,
                "anomaly_score": anomaly_score,
                "severity": severity,
                "model_version": prediction.get("model_version"),
                "status": "triggered",
            }
            if blast_radius_result is not None:
                payload.update(
                    {
                        "blast_radius": blast_radius_payload.get("blast_radius", [])
                        if blast_radius_payload
                        else [],
                        "suspected_root_service": blast_radius_result.suspected_root_service,
                        "root_cause_confidence": blast_radius_result.confidence,
                        "graph_analysis_version": blast_radius_result.algorithm_version,
                    }
                )

            if self.benchmarking_collector:
                payload["system_health"] = (
                    self.benchmarking_collector.get_health_metrics()
                )

            await self.telemetry_broadcaster.broadcast(
                telemetry_event(
                    "infrastructure.tracking_loop.triggered",
                    payload,
                    tenant_id=feature_vector.tenant_id,
                    owner_user_id=feature_vector.owner_user_id,
                    logical_id=f"incident:{feature_vector.tenant_id}:{feature_vector.owner_user_id}:{feature_vector.window_id}",
                )
            )
        except Exception as exc:
            logger.error(
                "Failed to broadcast tracking loop event exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )

    async def _run_graph_analysis(
        self,
        feature_vector: FeatureVector,
    ) -> BlastRadiusResult | None:
        """Run graph analysis with reliability isolation."""
        if not self.graph_scoring_settings.enabled:
            logger.debug("Graph scoring skipped: disabled")
            return None
        if self.graph_analysis_service is None:
            logger.debug("Graph scoring skipped: service unavailable")
            return None

        try:
            return await asyncio.wait_for(
                self.graph_analysis_service.analyze_anomaly(
                    feature_vector=feature_vector,
                ),
                timeout=self.graph_scoring_settings.timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning("Graph scoring timed out")
        except Exception as exc:
            logger.warning(
                "Graph scoring failed: %s",
                type(exc).__name__,
            )
        return None

"""Async worker for managing tracking infrastructure loops based on anomaly scores.

This worker receives anomaly scores from the machine learning pipeline and
triggers automated tracking and alert loops when thresholds are met.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

from ..core import redis as redis_state
from ..core.settings import GraphScoringSettings, get_graph_scoring_settings
from ..ml.anomaly_scoring import normalize_prediction_anomaly_score
from ..models import FeatureVector, PerformanceEvent
from ..repositories.tracking_repository import TrackingRepository
from ..schemas.alerting import IncidentAlertPayload
from ..schemas.blast_radius import BlastRadiusResult
from ..services.alerting import dispatch_incident_alert
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
        self.redis_client: Redis | None = None
        self._processed_count = 0
        self._error_count = 0
        self._last_processed_at: str | None = None

        logger.info("EventManager initialized")

    def set_redis_client(self, redis_client: Redis) -> None:
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
        """Enqueue a feature vector for evaluation without blocking."""
        try:
            self.queue.put_nowait(feature_vector)
            return True
        except asyncio.QueueFull:
            logger.warning(
                "EventManager queue is full; dropping feature vector event to protect memory"
            )
            return False

    def enqueue_performance_event(self, event: PerformanceEvent) -> bool:
        """Enqueue a performance event for alerting without blocking."""
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            logger.warning(
                "EventManager queue is full; dropping performance event to protect memory"
            )
            return False

    async def run(self) -> None:
        """Main worker loop that dequeues and evaluates events."""
        while self._running:
            try:
                event = await self.queue.get()
                if isinstance(event, FeatureVector):
                    await self._process_event(event)
                elif isinstance(event, PerformanceEvent):
                    await self._process_performance_event(event)
                self._processed_count += 1
                self._last_processed_at = datetime.now(timezone.utc).isoformat()
                self.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._error_count += 1
                logger.exception(
                    "EventManager encountered an error processing an event"
                )

    def get_stats(self) -> dict[str, Any]:
        """Return bounded worker state for readiness and Prometheus sampling."""
        return {
            "running": self._running,
            "processed_count": self._processed_count,
            "error_count": self._error_count,
            "dlq_count": 0,
            "queue_size": self.queue.qsize(),
            "last_processed_at": self._last_processed_at,
        }

    async def _process_performance_event(self, event: PerformanceEvent) -> None:
        """Process and broadcast a performance threshold breach alert."""
        try:
            payload = event.model_dump(mode="json")
            await self.telemetry_broadcaster.broadcast(
                telemetry_event(
                    "infrastructure.performance.alert",
                    payload,
                )
            )
            logger.warning(
                f"Performance alert triggered: {event.metric_name} = {event.current_value} (threshold {event.threshold})"
            )
        except Exception:
            logger.exception("Failed to broadcast performance event")

    async def _process_event(self, feature_vector: FeatureVector) -> None:
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
            await self._trigger_tracking_loop(feature_vector, anomaly_score, prediction)

    async def _trigger_tracking_loop(
        self,
        feature_vector: FeatureVector,
        anomaly_score: float,
        prediction: dict[str, Any],
    ) -> None:
        """Create a tracking loop in the database and emit an alert telemetry event."""
        blast_radius_result = await self._run_graph_analysis(feature_vector)
        blast_radius_payload = (
            blast_radius_result.model_dump(mode="json")
            if blast_radius_result is not None
            else None
        )

        # Persist to database
        try:
            await self.tracking_repository.persist_tracking_loop(
                window_id=feature_vector.window_id,
                anomaly_score=anomaly_score,
                status="ACTIVE",
                blast_radius=blast_radius_payload,
            )
        except Exception:
            logger.exception("Failed to persist tracking loop in EventManager")

        # Broadcast via WebSocket
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
                )
            )
        except Exception:
            logger.exception("Failed to broadcast tracking loop event")

        # Suppress and deduplicate repeated webhook alerts using Redis cooldown
        try:
            # Resolve the module-level pool at execution time as a fallback.
            # Importing ``_redis_pool`` by value would leave this worker with
            # None because startup replaces the pool during lifespan.
            redis = self.redis_client
            if redis is None and redis_state._redis_pool is not None:
                redis = Redis(connection_pool=redis_state._redis_pool)

            if redis is not None:
                service_dist = feature_vector.features.get("service_distribution", {})
                dominant_service = (
                    max(service_dist.items(), key=lambda x: x[1])[0]
                    if service_dist
                    else "unknown"
                )
                anomaly_type = "anomaly_spike"

                cooldown_key = f"alert_cooldown:{dominant_service}:{anomaly_type}"
                lock_acquired = await redis.set(cooldown_key, "1", nx=True, ex=900)

                if lock_acquired:
                    logger.info(
                        "Triggering webhook alert for %s (cooldown active for 15m)",
                        dominant_service,
                    )
                    alert_payload = IncidentAlertPayload(
                        incident_id=feature_vector.window_id,
                        root_cause_service=dominant_service,
                        affected_services=[dominant_service],
                        confidence_score=anomaly_score,
                        is_critical=(anomaly_score >= 0.7),
                    )
                    asyncio.create_task(
                        dispatch_incident_alert(alert_payload, redis_client=redis)
                    )
                else:
                    logger.debug(
                        "Webhook alert for %s suppressed by 15-minute cooldown",
                        dominant_service,
                    )
        except Exception:
            logger.exception("Failed to process webhook alert deduplication")

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

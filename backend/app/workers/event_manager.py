"""Async worker for managing tracking infrastructure loops based on anomaly scores.

This worker receives anomaly scores from the machine learning pipeline and
triggers automated tracking and alert loops when thresholds are met.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from ..models import FeatureVector
from ..repositories.tracking_repository import TrackingRepository
from ..services.telemetry import telemetry_event, telemetry_manager

logger = logging.getLogger("logsentinel.event_manager")


class EventManager:
    """Background worker that evaluates anomaly scores and automates tracking loops."""

    def __init__(
        self,
        tracking_repository: Optional[TrackingRepository] = None,
        anomaly_threshold: float = 0.75,
        max_queue_size: int = 10000,
    ) -> None:
        """Initialize the event manager.
        
        Args:
            tracking_repository: Repository to persist tracking loops.
            anomaly_threshold: Score above which a tracking loop is triggered.
            max_queue_size: Maximum size of the incoming queue.
        """
        self.tracking_repository = tracking_repository or TrackingRepository()
        self.anomaly_threshold = anomaly_threshold
        self.queue: asyncio.Queue[FeatureVector] = asyncio.Queue(maxsize=max_queue_size)
        
        self._task: asyncio.Task[None] | None = None
        self._running = False
        
        logger.info(
            "EventManager initialized with anomaly_threshold=%.2f",
            self.anomaly_threshold,
        )

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
            logger.warning("EventManager queue is full; dropping feature vector event to protect memory")
            return False

    async def run(self) -> None:
        """Main worker loop that dequeues and evaluates anomaly events."""
        while self._running:
            try:
                feature_vector = await self.queue.get()
                await self._process_event(feature_vector)
                self.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("EventManager encountered an error processing an event")

    async def _process_event(self, feature_vector: FeatureVector) -> None:
        """Evaluate a single feature vector and trigger tracking loops if needed."""
        prediction = feature_vector.anomaly_prediction
        if not isinstance(prediction, dict):
            return

        is_anomaly = prediction.get("is_anomaly")
        anomaly_score = prediction.get("anomaly_score")

        if is_anomaly is True and anomaly_score is not None:
            if anomaly_score >= self.anomaly_threshold:
                logger.info(
                    "Threshold met (%.3f >= %.3f). Triggering tracking loop for window_id=%s",
                    anomaly_score,
                    self.anomaly_threshold,
                    feature_vector.window_id,
                )
                await self._trigger_tracking_loop(feature_vector, anomaly_score, prediction)
            else:
                logger.debug(
                    "Anomaly detected but score (%.3f) is below threshold (%.3f)",
                    anomaly_score,
                    self.anomaly_threshold,
                )

    async def _trigger_tracking_loop(
        self, feature_vector: FeatureVector, anomaly_score: float, prediction: dict[str, Any]
    ) -> None:
        """Create a tracking loop in the database and emit an alert telemetry event."""
        # Persist to database
        try:
            await self.tracking_repository.persist_tracking_loop(
                window_id=feature_vector.window_id,
                anomaly_score=anomaly_score,
                status="triggered",
            )
        except Exception:
            logger.exception("Failed to persist tracking loop in EventManager")

        # Broadcast via WebSocket
        try:
            await telemetry_manager.broadcast(
                telemetry_event(
                    "infrastructure.tracking_loop.triggered",
                    {
                        "window_id": feature_vector.window_id,
                        "anomaly_score": anomaly_score,
                        "severity": prediction.get("severity"),
                        "model_version": prediction.get("model_version"),
                        "status": "triggered",
                    },
                )
            )
        except Exception:
            logger.exception("Failed to broadcast tracking loop event")

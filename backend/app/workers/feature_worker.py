"""Async worker for sliding window feature extraction from parsed logs.

This worker runs independently from the Drain3 parsing pipeline and extracts
features from log windows for downstream anomaly detection.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..ml.anomaly_detector import IsolationForestAnomalyDetector
from ..ml.feature_extractor import (
    SlidingWindowFeatureExtractor as SlidingWindowExtractor,
)
from ..ml.feature_extractor import WindowConfig
from ..models import FeatureVector, ParsedLog
from ..repositories.feature_repository import FeatureRepository
from ..services.telemetry import telemetry_event, telemetry_manager
from .event_manager import EventManager

logger = logging.getLogger("logsentinel.feature_worker")


class FeatureExtractionWorker:
    """Background worker that generates features from parsed log streams.
    
    This worker:
    1. Receives parsed logs from the Drain3 pipeline
    2. Buffers them in a sliding window extractor
    3. Periodically generates windows and extracts features
    4. Stores feature vectors for downstream ML processing
    """
    
    def __init__(
        self,
        window_config: WindowConfig | None = None,
        extraction_interval_seconds: float = 10.0,
        feature_buffer_size: int = 1000,
        anomaly_detector: IsolationForestAnomalyDetector | None = None,
        anomaly_model_path: str | Path | None = None,
        feature_repository: FeatureRepository | None = None,
        event_manager: EventManager | None = None,
    ) -> None:
        """Initialize the feature extraction worker.
        
        Args:
            window_config: Sliding window configuration
            extraction_interval_seconds: How often to generate windows
            feature_buffer_size: Max features to keep in memory
        """
        self.window_config = window_config or WindowConfig()
        self.extraction_interval_seconds = extraction_interval_seconds
        
        self.extractor = SlidingWindowExtractor(self.window_config)
        self.anomaly_model_path = Path(anomaly_model_path) if anomaly_model_path is not None else None
        self.model_load_error: str | None = None
        self.anomaly_detector = self._resolve_anomaly_detector(anomaly_detector, self.anomaly_model_path)
        self._feature_repository = feature_repository
        self.event_manager = event_manager
        
        # Buffer recent feature vectors for inspection/debugging
        self._feature_buffer: deque[FeatureVector] = deque(maxlen=feature_buffer_size)
        
        # Worker state
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._features_extracted = 0
        self._extraction_errors = 0
        self._last_extraction_at: str | None = None
        
        logger.info(
            "FeatureExtractionWorker initialized: interval=%ds window=%ds stride=%ds",
            self.extraction_interval_seconds,
            self.window_config.window_size_seconds,
            self.window_config.stride_seconds,
        )
    
    def start(self) -> None:
        """Start the background feature extraction loop."""
        if self._task and not self._task.done():
            logger.warning("FeatureExtractionWorker already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self.run(), name="feature-extraction-worker")
        logger.info("FeatureExtractionWorker started")
    
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
        
        logger.info("FeatureExtractionWorker stopped")
    
    async def run(self) -> None:
        """Main worker loop that periodically extracts features."""
        while self._running:
            try:
                await asyncio.sleep(self.extraction_interval_seconds)
                await self.extract_pending_features()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._extraction_errors += 1
                logger.exception("Feature extraction worker encountered an error")
    
    async def extract_pending_features(self, current_time: datetime | None = None) -> list[FeatureVector]:
        """Generate all pending windows and extract features.
        
        Returns:
            List of extracted FeatureVector objects
        """
        try:
            # Get all windows ready for feature extraction
            windows = self.extractor.get_pending_windows(current_time=current_time)
            
            if not windows:
                return []
            
            features: list[FeatureVector] = []
            
            for window in windows:
                try:
                    feature_vector = self.extractor.extract_features(window)
                    try:
                        if self.anomaly_detector is not None and self.anomaly_detector.model is not None:
                            feature_vector.anomaly_prediction = await asyncio.to_thread(
                                self.anomaly_detector.predict, feature_vector
                            )
                    except Exception:
                        logger.exception("Failed to run anomaly prediction for window %s", window.window_id)
                    self._feature_buffer.append(feature_vector)
                    features.append(feature_vector)
                    self._features_extracted += 1
                    self._schedule_feature_events(feature_vector)
                    self._schedule_persist(feature_vector)
                    
                    if self.event_manager is not None:
                        self.event_manager.enqueue_feature_vector(feature_vector)
                except Exception:
                    self._extraction_errors += 1
                    logger.exception(
                        "Failed to extract features from window %s",
                        window.window_id,
                    )
            
            self._last_extraction_at = datetime.now(timezone.utc).isoformat()
            
            if features:
                logger.info(
                    "Extracted %d feature vectors from %d windows",
                    len(features),
                    len(windows),
                )
            
            return features
        
        except Exception:
            self._extraction_errors += 1
            logger.exception("Failed to extract pending features")
            return []
    
    def add_parsed_log(self, log: ParsedLog) -> None:
        """Add a parsed log to the feature extractor buffer.
        
        This is the main integration point with the Drain3 pipeline.
        """
        self.extractor.add_log(log)
    
    def add_parsed_logs(self, logs: list[ParsedLog]) -> None:
        """Add multiple parsed logs to the buffer."""
        self.extractor.add_logs(logs)
    
    def get_recent_features(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent feature vectors as dicts, newest first."""
        safe_limit = max(0, limit)
        recent = list(self._feature_buffer)[-safe_limit:][::-1] if safe_limit else []
        return [fv.model_dump(mode="json") for fv in recent]
    
    def get_stats(self) -> dict[str, Any]:
        """Return worker statistics and extractor state."""
        return {
            "running": self._running,
            "extraction_interval_seconds": self.extraction_interval_seconds,
            "features_extracted": self._features_extracted,
            "extraction_errors": self._extraction_errors,
            "last_extraction_at": self._last_extraction_at,
            "feature_buffer_size": len(self._feature_buffer),
            "model": self.get_model_health(),
            "extractor": self.extractor.get_stats(),
        }

    def get_model_health(self) -> dict[str, Any]:
        """Return bounded model lifecycle state for health/metrics adapters."""
        if self.anomaly_detector is not None:
            health = self.anomaly_detector.get_health(self.anomaly_model_path)
            if self.model_load_error:
                health["model_load_error"] = self.model_load_error
            return health

        return {
            "model_loaded": False,
            "model_version": None,
            "model_age_seconds": None,
            "artifact_path": str(self.anomaly_model_path) if self.anomaly_model_path else None,
            "inference_total": 0,
            "inference_errors_total": 0,
            "anomalies_total": 0,
            "model_load_error": self.model_load_error,
        }
    
    def clear_buffers(self) -> dict[str, int]:
        """Clear all internal buffers (for testing/debugging)."""
        logs_removed = self.extractor.clear_buffer()
        features_removed = len(self._feature_buffer)
        self._feature_buffer.clear()
        
        return {
            "logs_removed": logs_removed,
            "features_removed": features_removed,
        }

    def _resolve_anomaly_detector(
        self,
        anomaly_detector: IsolationForestAnomalyDetector | None,
        anomaly_model_path: str | Path | None,
    ) -> IsolationForestAnomalyDetector | None:
        if anomaly_detector is not None:
            return anomaly_detector

        if anomaly_model_path is None:
            return None

        model_path = Path(anomaly_model_path)
        if not model_path.exists():
            logger.warning("Isolation Forest artifact is absent at %s", model_path)
            return None

        try:
            return IsolationForestAnomalyDetector.load_model(model_path)
        except Exception as exc:
            self.model_load_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Failed to load Isolation Forest artifact from %s", model_path)
            return None

    def _schedule_feature_events(self, feature_vector: FeatureVector) -> None:
        self._schedule_telemetry_event(
            telemetry_event(
                "feature.window.closed",
                {
                    "window_id": feature_vector.window_id,
                    "window_start": _serialize_datetime(feature_vector.window_start),
                    "window_end": _serialize_datetime(feature_vector.window_end),
                    "total_log_count": feature_vector.log_count,
                    "error_count": feature_vector.error_count,
                    "warning_count": feature_vector.warning_count,
                    "error_ratio": feature_vector.features.get("error_ratio"),
                    "active_services": feature_vector.features.get("active_services"),
                    "unique_templates": feature_vector.unique_templates,
                    "burst_indicator": feature_vector.features.get("burst_indicator"),
                },
            )
        )

        prediction = feature_vector.anomaly_prediction
        if isinstance(prediction, dict) and prediction.get("is_anomaly") is True:
            self._schedule_telemetry_event(
                telemetry_event(
                    "anomaly.detected",
                    {
                        "window_id": feature_vector.window_id,
                        "anomaly_score": prediction.get("anomaly_score"),
                        "severity": prediction.get("severity"),
                        "model_version": prediction.get("model_version"),
                    },
                )
            )

    def _schedule_telemetry_event(self, event: dict[str, Any]) -> None:
        try:
            asyncio.create_task(telemetry_manager.broadcast(event))
        except RuntimeError:
            logger.debug("No running event loop available for feature telemetry broadcast")

    def _schedule_persist(self, feature_vector: FeatureVector) -> None:
        """Persist the feature vector to the database if a repository is configured."""
        if self._feature_repository is None:
            return
        try:
            asyncio.create_task(self._feature_repository.persist_feature_vector(feature_vector))
        except RuntimeError:
            logger.debug("No running event loop available for feature persistence")


def _serialize_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value

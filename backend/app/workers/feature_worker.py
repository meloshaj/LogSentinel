"""Async worker for sliding window feature extraction from parsed logs.

This worker runs independently from the Drain3 parsing pipeline and extracts
features from log windows for downstream anomaly detection.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from ..ml.feature_extraction import SlidingWindowExtractor, WindowConfig
from ..models import FeatureVector, ParsedLog

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
        window_config: Optional[WindowConfig] = None,
        extraction_interval_seconds: float = 10.0,
        feature_buffer_size: int = 1000,
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
        
        # Buffer recent feature vectors for inspection/debugging
        self._feature_buffer: deque[FeatureVector] = deque(maxlen=feature_buffer_size)
        
        # Worker state
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._features_extracted = 0
        self._extraction_errors = 0
        self._last_extraction_at: Optional[str] = None
        
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
    
    async def extract_pending_features(self) -> list[FeatureVector]:
        """Generate all pending windows and extract features.
        
        Returns:
            List of extracted FeatureVector objects
        """
        try:
            # Get all windows ready for feature extraction
            windows = self.extractor.get_pending_windows()
            
            if not windows:
                return []
            
            features: list[FeatureVector] = []
            
            for window in windows:
                try:
                    feature_vector = self.extractor.extract_features(window)
                    self._feature_buffer.append(feature_vector)
                    features.append(feature_vector)
                    self._features_extracted += 1
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
            "extractor": self.extractor.get_stats(),
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

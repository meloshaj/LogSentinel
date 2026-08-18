"""Sliding window feature extraction for parsed log streams.

This module converts a continuous stream of parsed logs into fixed-size
numerical feature vectors suitable for downstream ML models. The implementation
focuses on streaming-friendly processing, configurable time windows, and clear
separation from any anomaly detection logic.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ..models import FeatureVector, LogWindow, ParsedLog

logger = logging.getLogger("logsentinel.feature_extractor")


class WindowConfig(BaseModel):
    """Configuration for sliding-window feature extraction."""

    window_size_seconds: int = Field(default=10, ge=1, description="Size of each time window")
    stride_seconds: int = Field(default=5, ge=1, description="Spacing between windows")
    min_logs_per_window: int = Field(default=1, ge=0, description="Minimum logs required to emit a window")
    max_logs_per_window: int = Field(default=10000, ge=1, description="Maximum logs to keep per window")
    service_filter: str | None = Field(default=None, description="Optional service filter")

    def validate_config(self) -> None:
        """Validate the window configuration."""
        if self.stride_seconds > self.window_size_seconds:
            logger.warning(
                "Stride (%ds) exceeds window size (%ds); windows may be sparse",
                self.stride_seconds,
                self.window_size_seconds,
            )


class SlidingWindowFeatureExtractor:
    """Extract fixed-size feature vectors from a stream of parsed logs.

    The extractor maintains a single active window and closes it when a new log
    arrives outside the current window boundary. Each closed window is converted
    to a structured feature vector that is independent from any anomaly model.
    """

    def __init__(self, config: WindowConfig | None = None) -> None:
        self.config = config or WindowConfig()
        self.config.validate_config()

        self._log_buffer: list[ParsedLog] = []
        self._logs_processed = 0
        self._windows_generated = 0
        self._last_window_end: datetime | None = None

    def add_log(self, log: ParsedLog) -> None:
        """Add a parsed log to the buffer."""
        self._log_buffer.append(log)
        self._logs_processed += 1

    def add_logs(self, logs: list[ParsedLog]) -> None:
        """Add multiple parsed logs to the buffer."""
        for log in logs:
            self.add_log(log)

    def get_pending_windows(self, current_time: datetime | None = None) -> list[LogWindow]:
        """Generate closed windows from the buffered log history."""
        if not self._log_buffer:
            return []

        current_time = current_time or datetime.now(timezone.utc)
        windows: list[LogWindow] = []
        start_time = self._last_window_end or self._align_to_window(
            self._log_buffer[0].timestamp
        )

        while True:
            end_time = start_time + timedelta(seconds=self.config.window_size_seconds)
            if end_time > current_time:
                break
            
            window_logs = [
                log for log in self._log_buffer
                if start_time <= log.timestamp < end_time
                and (not self.config.service_filter or log.service == self.config.service_filter)
            ]
            
            if len(window_logs) >= self.config.min_logs_per_window:
                windows.append(
                    LogWindow(
                        window_id=f"window-{uuid4().hex[:16]}",
                        start_time=start_time,
                        end_time=end_time,
                        logs=window_logs,
                        service=self.config.service_filter,
                    )
                )
                self._windows_generated += 1
            
            start_time = start_time + timedelta(seconds=self.config.stride_seconds)
            self._last_window_end = start_time

        if self._last_window_end:
            self._log_buffer = [log for log in self._log_buffer if log.timestamp >= self._last_window_end]

        return windows

    def extract_features(self, window: LogWindow | None) -> FeatureVector:
        """Extract a fixed-size feature vector from a log window."""
        if window is None or not window.logs:
            return self._empty_feature_vector()

        logs = [log for log in window.logs if not self.config.service_filter or log.service == self.config.service_filter]
        if not logs:
            return self._empty_feature_vector()

        level_counts = Counter(log.level.lower() for log in logs)
        info_count = sum(v for k, v in level_counts.items() if k in {"info", "information", "notice"})
        warning_count = sum(v for k, v in level_counts.items() if k in {"warn", "warning"})
        error_count = sum(v for k, v in level_counts.items() if k in {"error", "critical", "fatal", "exception"})
        log_count = len(logs)
        error_ratio = error_count / log_count if log_count else 0.0

        service_distribution = Counter(log.service for log in logs)
        active_services = len(service_distribution)
        dominant_service = max(service_distribution.items(), key=lambda item: (item[1], item[0]), default=(None, 0))[0]

        template_distribution = Counter(log.template_id for log in logs)
        unique_templates = len(template_distribution)
        dominant_template = max(template_distribution.items(), key=lambda item: (item[1], item[0]), default=(None, 0))[0]

        duration_seconds = window.duration_seconds()
        logs_per_second = log_count / duration_seconds if duration_seconds > 0 else 0.0
        avg_logs_per_minute = logs_per_second * 60.0
        burst_indicator = 1.0 if logs_per_second >= 2.0 else 0.0

        features = {
            "log_count": float(log_count),
            "info_count": float(info_count),
            "warning_count": float(warning_count),
            "error_count": float(error_count),
            "error_ratio": float(error_ratio),
            "active_services": float(active_services),
            "unique_templates": float(unique_templates),
            "dominant_service_count": float(service_distribution.get(dominant_service, 0)),
            "dominant_template_count": float(template_distribution.get(dominant_template, 0)),
            "logs_per_second": float(logs_per_second),
            "avg_logs_per_minute": float(avg_logs_per_minute),
            "burst_indicator": float(burst_indicator),
        }

        feature_names = list(features.keys())
        feature_array = [float(features[name]) for name in feature_names]

        return FeatureVector(
            window_id=window.window_id,
            timestamp=datetime.now(timezone.utc),
            window_start=window.start_time,
            window_end=window.end_time,
            log_count=log_count,
            unique_templates=unique_templates,
            error_count=error_count,
            warning_count=warning_count,
            template_frequencies={
                template_id: count / log_count if log_count else 0.0
                for template_id, count in template_distribution.items()
            },
            template_entropy=self._compute_entropy(list(template_distribution.values()), log_count),
            service_distribution=dict(service_distribution),
            logs_per_second=logs_per_second,
            feature_array=feature_array,
            feature_names=feature_names,
            features={
                **features,
                "dominant_service": dominant_service,
                "dominant_template": dominant_template,
                "service_counts": {service: count for service, count in service_distribution.items()},
                "template_counts": {template_id: count for template_id, count in template_distribution.items()},
            },
        )

    def get_stats(self) -> dict[str, Any]:
        """Return extractor statistics for monitoring and debugging."""
        return {
            "config": self.config.model_dump(),
            "logs_processed": self._logs_processed,
            "windows_generated": self._windows_generated,
            "last_window_end": self._last_window_end.isoformat() if self._last_window_end else None,
            "current_buffer_size": len(self._log_buffer),
        }

    def clear_buffer(self) -> int:
        """Clear the log buffer and return the number of logs removed."""
        removed = len(self._log_buffer)
        self._log_buffer.clear()
        self._last_window_end = None
        return removed

    def _align_to_window(self, timestamp: datetime) -> datetime:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        seconds_since_epoch = int((timestamp.astimezone(timezone.utc) - epoch).total_seconds())
        window_seconds = (seconds_since_epoch // self.config.window_size_seconds) * self.config.window_size_seconds
        return datetime.fromtimestamp(window_seconds, tz=timezone.utc)

    def _empty_feature_vector(self) -> FeatureVector:
        """Create a zero-filled feature vector for empty windows."""
        return FeatureVector(
            window_id=f"window-{uuid4().hex[:16]}",
            timestamp=datetime.now(timezone.utc),
            window_start=None,
            window_end=None,
            log_count=0,
            unique_templates=0,
            error_count=0,
            warning_count=0,
            template_frequencies={},
            template_entropy=0.0,
            service_distribution={},
            logs_per_second=0.0,
            feature_array=[0.0] * 12,
            feature_names=[
                "log_count",
                "info_count",
                "warning_count",
                "error_count",
                "error_ratio",
                "active_services",
                "unique_templates",
                "dominant_service_count",
                "dominant_template_count",
                "logs_per_second",
                "avg_logs_per_minute",
                "burst_indicator",
            ],
            features={
                "log_count": 0.0,
                "info_count": 0.0,
                "warning_count": 0.0,
                "error_count": 0.0,
                "error_ratio": 0.0,
                "active_services": 0.0,
                "unique_templates": 0.0,
                "dominant_service_count": 0.0,
                "dominant_template_count": 0.0,
                "logs_per_second": 0.0,
                "avg_logs_per_minute": 0.0,
                "burst_indicator": 0.0,
                "dominant_service": None,
                "dominant_template": None,
                "service_counts": {},
                "template_counts": {},
            },
        )

    @staticmethod
    def _compute_entropy(template_counts: list[int], log_count: int) -> float:
        """Compute Shannon entropy for a template distribution."""
        if log_count <= 0:
            return 0.0
        probabilities = [count / log_count for count in template_counts if count > 0]
        if not probabilities:
            return 0.0
        entropy = 0.0
        for probability in probabilities:
            entropy -= probability * math.log2(probability)
        return entropy

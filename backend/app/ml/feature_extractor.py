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
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from ..models import FeatureVector, LogWindow, ParsedLog

logger = logging.getLogger("logsentinel.feature_extractor")


class WindowConfig(BaseModel):
    """Configuration for sliding-window feature extraction."""

    window_size_seconds: int = Field(default=60, ge=1, description="Size of each time window")
    stride_seconds: int = Field(default=30, ge=1, description="Spacing between windows")
    min_logs_per_window: int = Field(default=1, ge=0, description="Minimum logs required to emit a window")
    max_logs_per_window: int = Field(default=10000, ge=1, description="Maximum logs to keep per window")
    service_filter: Optional[str] = Field(default=None, description="Optional service filter")

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

    def __init__(self, config: Optional[WindowConfig] = None) -> None:
        self.config = config or WindowConfig()
        self.config.validate_config()

        self._current_window_logs: list[ParsedLog] = []
        self._current_window_start: Optional[datetime] = None
        self._current_window_end: Optional[datetime] = None
        self._log_buffer: list[ParsedLog] = []
        self._logs_processed = 0
        self._windows_generated = 0
        self._last_window_end: Optional[datetime] = None

    def add_log(self, log: ParsedLog) -> None:
        """Add a parsed log to the active window."""
        self._log_buffer.append(log)
        if not self._current_window_logs:
            self._start_new_window(log.timestamp)
        elif self._current_window_end is not None and log.timestamp >= self._current_window_end:
            self.close_window(log.timestamp)
            self._start_new_window(log.timestamp)

        self._current_window_logs.append(log)
        self._logs_processed += 1

    def add_logs(self, logs: list[ParsedLog]) -> None:
        """Add multiple parsed logs to the current stream."""
        for log in logs:
            self.add_log(log)

    def get_pending_windows(self, current_time: Optional[datetime] = None) -> list[LogWindow]:
        """Generate closed windows from the buffered log history."""
        if not self._log_buffer:
            return []

        current_time = current_time or datetime.now(timezone.utc)
        windows: list[LogWindow] = []
        start_time = self._align_to_window(self._log_buffer[0].timestamp)

        while True:
            end_time = start_time + timedelta(seconds=self.config.window_size_seconds)
            if end_time > current_time:
                break
            window_logs = [
                log for log in self._log_buffer
                if start_time <= log.timestamp < end_time
                and (not self.config.service_filter or log.service == self.config.service_filter)
            ]
            if window_logs or self.config.min_logs_per_window == 0:
                windows.append(
                    LogWindow(
                        window_id=f"window-{uuid4().hex[:16]}",
                        start_time=start_time,
                        end_time=end_time,
                        logs=window_logs,
                        service=self.config.service_filter,
                    )
                )
            start_time = end_time

        return windows

    def get_current_window(self) -> Optional[LogWindow]:
        """Return the currently active window, if any."""
        if not self._current_window_logs or self._current_window_start is None or self._current_window_end is None:
            return None
        return LogWindow(
            window_id=f"window-{uuid4().hex[:16]}",
            start_time=self._current_window_start,
            end_time=self._current_window_end,
            logs=list(self._current_window_logs),
            service=self.config.service_filter,
        )

    def close_window(self, end_time: Optional[datetime] = None) -> Optional[FeatureVector]:
        """Close the active window and return its extracted feature vector."""
        if not self._current_window_logs:
            return None

        window = self._build_window(end_time)
        feature_vector = self.extract_features(window)
        self._windows_generated += 1
        self._last_window_end = window.end_time

        self._current_window_logs = []
        self._current_window_start = None
        self._current_window_end = None

        return feature_vector

    def extract_features(self, window: Optional[LogWindow]) -> FeatureVector:
        """Extract a fixed-size feature vector from a log window."""
        if window is None or not window.logs:
            return self._empty_feature_vector()

        logs = [log for log in window.logs if not self.config.service_filter or log.service == self.config.service_filter]
        if not logs:
            return self._empty_feature_vector()

        level_counts = Counter(log.level.lower() for log in logs)
        info_count = sum(1 for log in logs if log.level.lower() in {"info", "information", "notice"})
        warning_count = level_counts.get("warning", 0)
        error_count = level_counts.get("error", 0)
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
            "current_window_size": len(self._current_window_logs),
        }

    def clear_buffer(self) -> int:
        """Clear the active window and return the number of logs removed."""
        removed = len(self._current_window_logs)
        self._current_window_logs.clear()
        self._current_window_end = None
        self._current_window_start = None
        return removed

    def _start_new_window(self, timestamp: datetime) -> None:
        start_time = self._align_to_window(timestamp)
        end_time = start_time + timedelta(seconds=self.config.window_size_seconds)
        self._current_window_start = start_time
        self._current_window_end = end_time

    def _align_to_window(self, timestamp: datetime) -> datetime:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        seconds_since_epoch = int((timestamp.astimezone(timezone.utc) - epoch).total_seconds())
        window_seconds = (seconds_since_epoch // self.config.window_size_seconds) * self.config.window_size_seconds
        return datetime.fromtimestamp(window_seconds, tz=timezone.utc)

    def _build_window(self, end_time: Optional[datetime] = None) -> LogWindow:
        if self._current_window_start is None or self._current_window_end is None:
            raise ValueError("No active window to close")
        close_time = end_time or self._current_window_end
        return LogWindow(
            window_id=f"window-{uuid4().hex[:16]}",
            start_time=self._current_window_start,
            end_time=close_time,
            logs=list(self._current_window_logs),
            service=self.config.service_filter,
        )

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

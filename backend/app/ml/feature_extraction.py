"""Sliding window feature extraction for log sequences.

This module implements time-based sliding window extraction and statistical
feature computation from parsed log streams.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ..models import FeatureVector, LogWindow, ParsedLog

logger = logging.getLogger("logsentinel.feature_extraction")


class WindowConfig(BaseModel):
    """Configuration for sliding window extraction."""

    window_size_seconds: int = Field(
        default=60,
        ge=1,
        description="Window size in seconds",
    )
    stride_seconds: int = Field(
        default=30,
        ge=1,
        description="Stride (step) size in seconds between windows",
    )
    min_logs_per_window: int = Field(
        default=1,
        ge=0,
        description="Minimum logs required to emit a window (0 = no minimum)",
    )
    max_logs_per_window: int = Field(
        default=10000,
        ge=1,
        description="Maximum logs to include in a single window",
    )
    service_filter: str | None = Field(
        None,
        description="Optional service name filter",
    )

    def validate_config(self) -> None:
        """Validate window configuration."""
        if self.stride_seconds > self.window_size_seconds:
            logger.warning(
                "Stride (%ds) > window size (%ds) will create gaps between windows",
                self.stride_seconds,
                self.window_size_seconds,
            )


class SlidingWindowExtractor:
    """Extracts sliding windows and features from parsed log streams.

    This class maintains a buffer of recent logs and generates overlapping
    time-based windows for feature extraction.
    """

    def __init__(self, config: WindowConfig) -> None:
        self.config = config
        self.config.validate_config()

        # Buffer to hold recent logs (sized to hold at least 2 full windows)
        buffer_size = max(20000, self.config.max_logs_per_window * 2)
        self._log_buffer: deque[ParsedLog] = deque(maxlen=buffer_size)

        # Track window generation
        self._last_window_end: datetime | None = None
        self._windows_generated = 0
        self._logs_processed = 0

        logger.info(
            "SlidingWindowExtractor initialized: window=%ds stride=%ds buffer=%d",
            self.config.window_size_seconds,
            self.config.stride_seconds,
            buffer_size,
        )

    def add_log(self, log: ParsedLog) -> None:
        """Add a parsed log to the internal buffer."""
        self._log_buffer.append(log)
        self._logs_processed += 1

    def add_logs(self, logs: list[ParsedLog]) -> None:
        """Add multiple parsed logs to the buffer."""
        self._log_buffer.extend(logs)
        self._logs_processed += len(logs)

    def get_pending_windows(
        self,
        current_time: datetime | None = None,
    ) -> list[LogWindow]:
        """Generate all pending windows up to current_time.

        Args:
            current_time: Upper bound for window generation (defaults to now)

        Returns:
            List of LogWindow objects ready for feature extraction
        """
        if not self._log_buffer:
            return []

        current_time = current_time or datetime.now(timezone.utc)

        # Determine the start time for window generation
        if self._last_window_end is None:
            # First window starts at the earliest log timestamp
            start_time = self._log_buffer[0].timestamp
        else:
            # Next window starts stride_seconds after the last window ended
            start_time = self._last_window_end

        windows: list[LogWindow] = []

        while True:
            end_time = start_time + timedelta(seconds=self.config.window_size_seconds)

            # Don't generate windows beyond current_time
            if end_time > current_time:
                break

            window = self._create_window(start_time, end_time)

            # Only emit window if it meets minimum log count
            if (
                self.config.min_logs_per_window == 0
                or window.log_count() >= self.config.min_logs_per_window
            ):
                windows.append(window)
                self._windows_generated += 1

            # Move to next window position
            start_time = start_time + timedelta(seconds=self.config.stride_seconds)
            self._last_window_end = end_time

        return windows

    def _create_window(self, start_time: datetime, end_time: datetime) -> LogWindow:
        """Create a LogWindow from logs in the specified time range."""
        window_logs: list[ParsedLog] = []

        for log in self._log_buffer:
            # Check time bounds
            if log.timestamp < start_time:
                continue
            if log.timestamp >= end_time:
                # Since buffer is chronological, we can stop early
                if window_logs:
                    break
                continue

            # Apply service filter if configured
            if self.config.service_filter and log.service != self.config.service_filter:
                continue

            # Enforce max logs per window
            if len(window_logs) >= self.config.max_logs_per_window:
                logger.warning(
                    "Window exceeded max_logs_per_window (%d), truncating",
                    self.config.max_logs_per_window,
                )
                break

            window_logs.append(log)

        return LogWindow(
            window_id=f"window-{uuid4().hex[:16]}",
            start_time=start_time,
            end_time=end_time,
            logs=window_logs,
            service=self.config.service_filter,
        )

    def extract_features(self, window: LogWindow) -> FeatureVector:
        """Extract statistical features from a log window.

        Args:
            window: LogWindow to extract features from

        Returns:
            FeatureVector containing computed features
        """
        log_count = window.log_count()

        if log_count == 0:
            return self._empty_feature_vector(window)

        # Template distribution and entropy
        template_dist = window.template_distribution()
        unique_templates = len(template_dist)
        template_frequencies = {
            tid: count / log_count for tid, count in template_dist.items()
        }
        template_entropy = self._compute_entropy(list(template_frequencies.values()))

        # Level distribution
        level_counts = Counter(log.level.lower() for log in window.logs)
        error_count = level_counts.get("error", 0)
        warning_count = level_counts.get("warning", 0)

        # Service distribution
        service_distribution = Counter(log.service for log in window.logs)

        # Temporal features
        duration = window.duration_seconds()
        logs_per_second = log_count / duration if duration > 0 else 0.0

        # Build feature array for ML models
        feature_array, feature_names = self._build_feature_array(
            log_count=log_count,
            unique_templates=unique_templates,
            error_count=error_count,
            warning_count=warning_count,
            template_entropy=template_entropy,
            logs_per_second=logs_per_second,
            num_services=len(service_distribution),
        )

        return FeatureVector(  # type: ignore
            window_id=window.window_id,
            timestamp=datetime.now(timezone.utc),
            log_count=log_count,
            unique_templates=unique_templates,
            error_count=error_count,
            warning_count=warning_count,
            template_frequencies=template_frequencies,
            template_entropy=template_entropy,
            service_distribution=dict(service_distribution),
            logs_per_second=logs_per_second,
            feature_array=feature_array,
            feature_names=feature_names,
        )

    def _empty_feature_vector(self, window: LogWindow) -> FeatureVector:
        """Create a zero-filled feature vector for empty windows."""
        return FeatureVector(  # type: ignore
            window_id=window.window_id,
            timestamp=datetime.now(timezone.utc),
            log_count=0,
            unique_templates=0,
            error_count=0,
            warning_count=0,
            template_frequencies={},
            template_entropy=0.0,
            service_distribution={},
            logs_per_second=0.0,
            feature_array=[0.0] * 7,
            feature_names=[
                "log_count",
                "unique_templates",
                "error_count",
                "warning_count",
                "template_entropy",
                "logs_per_second",
                "num_services",
            ],
        )

    def _build_feature_array(
        self,
        log_count: int,
        unique_templates: int,
        error_count: int,
        warning_count: int,
        template_entropy: float,
        logs_per_second: float,
        num_services: int,
    ) -> tuple[list[float], list[str]]:
        """Build a flattened numerical feature array for ML models."""
        feature_names = [
            "log_count",
            "unique_templates",
            "error_count",
            "warning_count",
            "template_entropy",
            "logs_per_second",
            "num_services",
        ]

        feature_array = [
            float(log_count),
            float(unique_templates),
            float(error_count),
            float(warning_count),
            float(template_entropy),
            float(logs_per_second),
            float(num_services),
        ]

        return feature_array, feature_names

    @staticmethod
    def _compute_entropy(probabilities: list[float]) -> float:
        """Compute Shannon entropy from probability distribution."""
        if not probabilities:
            return 0.0

        entropy = 0.0
        for p in probabilities:
            if p > 0:
                entropy -= p * math.log2(p)

        return entropy

    def get_stats(self) -> dict[str, Any]:
        """Return extractor statistics."""
        return {
            "config": self.config.model_dump(),
            "buffer_size": len(self._log_buffer),
            "logs_processed": self._logs_processed,
            "windows_generated": self._windows_generated,
            "last_window_end": self._last_window_end.isoformat()
            if self._last_window_end
            else None,
        }

    def clear_buffer(self) -> int:
        """Clear the log buffer and return the number of logs removed."""
        removed = len(self._log_buffer)
        self._log_buffer.clear()
        return removed

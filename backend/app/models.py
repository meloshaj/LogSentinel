"""Pydantic models for LogSentinel data structures."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ParsedLog(BaseModel):
    """Standardized parsed log structure from Drain3 pipeline.
    
    This model ensures type safety and validation for logs that have been
    processed through the Drain3 template mining pipeline.

    The class also exposes dict-style access so the existing parser/worker
    tests and downstream code can continue using the older contract.
    """

    # Core log fields
    id: str = Field(
        ...,
        description="Unique 128-bit lexicographically sortable identifier (ULID)",
    )
    timestamp: datetime = Field(
        ...,
        description="Timestamp when the log event was emitted",
    )
    service: str = Field(
        ...,
        min_length=1,
        description="Name of the service that emitted the log",
    )
    level: str = Field(
        ...,
        min_length=1,
        description="Log severity level (info, warning, error, etc.)",
    )
    raw_message: str = Field(
        ...,
        min_length=1,
        description="Original unprocessed log message",
    )
    
    # Drain3 template fields
    template_id: str = Field(
        ...,
        description="Drain3 cluster ID for the log template",
    )
    template_text: Optional[str] = Field(
        None,
        description="Extracted log template with parameters replaced by wildcards",
    )
    parameters: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Extracted parameters from the log message",
    )
    cluster_size: Optional[int] = Field(
        None,
        ge=0,
        description="Number of logs in the Drain3 cluster",
    )
    change_type: Optional[str] = Field(
        None,
        description="Drain3 change type (none, cluster_created, cluster_template_changed)",
    )
    
    # Optional metadata fields
    source: Optional[str] = Field(
        None,
        description="Source system or ingestion point",
    )
    environment: Optional[str] = Field(
        None,
        description="Deployment environment (development, staging, production)",
    )
    correlation_id: Optional[str] = Field(
        None,
        description="Distributed trace correlation identifier",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional structured metadata",
    )
    
    # Processing timestamps
    parsed_at: Optional[datetime] = Field(
        None,
        description="Timestamp when Drain3 parsing completed",
    )
    
    model_config = ConfigDict(validate_assignment=True)

    def __getitem__(self, item: str) -> Any:
        """Enable dict-style access for compatibility with existing tests."""
        return getattr(self, item)

    def __iter__(self):
        """Allow iteration over the legacy parser field names expected by tests."""
        return iter(
            [
                "id",
                "raw_message",
                "template_id",
                "template_text",
                "cluster_size",
                "change_type",
                "parameters",
                "metadata",
                "parsed_at",
            ]
        )

    def keys(self) -> list[str]:
        """Return the legacy parser field names expected by tests."""
        return [
            "id",
            "raw_message",
            "template_id",
            "template_text",
            "cluster_size",
            "change_type",
            "parameters",
            "metadata",
            "parsed_at",
        ]


class LogWindow(BaseModel):
    """A time-based sliding window of parsed logs.
    
    Used for feature extraction and anomaly detection over temporal sequences.
    """
    
    window_id: str = Field(
        ...,
        description="Unique identifier for this window",
    )
    start_time: datetime = Field(
        ...,
        description="Window start timestamp (inclusive)",
    )
    end_time: datetime = Field(
        ...,
        description="Window end timestamp (exclusive)",
    )
    logs: list[ParsedLog] = Field(
        default_factory=list,
        description="Parsed logs within this time window",
    )
    service: Optional[str] = Field(
        None,
        description="Service filter applied to this window (if any)",
    )
    
    def log_count(self) -> int:
        """Return the number of logs in this window."""
        return len(self.logs)
    
    def duration_seconds(self) -> float:
        """Return the window duration in seconds."""
        return (self.end_time - self.start_time).total_seconds()
    
    def template_distribution(self) -> dict[str, int]:
        """Return distribution of template IDs in this window."""
        distribution: dict[str, int] = {}
        for log in self.logs:
            distribution[log.template_id] = distribution.get(log.template_id, 0) + 1
        return distribution
    
    model_config = ConfigDict()


class FeatureVector(BaseModel):
    """Feature vector extracted from a log window for ML processing.
    
    Contains both statistical and semantic features derived from log patterns.
    """
    
    window_id: str = Field(
        ...,
        description="Reference to the source LogWindow",
    )
    timestamp: datetime = Field(
        ...,
        description="Feature vector creation timestamp",
    )
    window_start: Optional[datetime] = Field(
        None,
        description="Inclusive start of the source window",
    )
    window_end: Optional[datetime] = Field(
        None,
        description="Exclusive end of the source window",
    )
    
    # Statistical features
    log_count: int = Field(
        ...,
        ge=0,
        description="Total number of logs in the window",
    )
    unique_templates: int = Field(
        ...,
        ge=0,
        description="Number of unique log templates",
    )
    error_count: int = Field(
        default=0,
        ge=0,
        description="Number of error-level logs",
    )
    warning_count: int = Field(
        default=0,
        ge=0,
        description="Number of warning-level logs",
    )
    
    # Template features
    template_frequencies: dict[str, float] = Field(
        default_factory=dict,
        description="Normalized frequency of each template ID",
    )
    template_entropy: Optional[float] = Field(
        None,
        ge=0.0,
        description="Shannon entropy of template distribution",
    )
    
    # Service features
    service_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Distribution of logs across services",
    )
    
    # Temporal features
    logs_per_second: Optional[float] = Field(
        None,
        ge=0.0,
        description="Average log rate in this window",
    )
    
    # Raw feature array for ML models
    feature_array: Optional[list[float]] = Field(
        None,
        description="Flattened numerical feature array for ML models",
    )
    feature_names: Optional[list[str]] = Field(
        None,
        description="Names corresponding to feature_array elements",
    )
    anomaly_prediction: Optional[dict[str, Any]] = Field(
        None,
        description="Structured anomaly detection output for this feature vector",
    )
    features: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional structured feature values for inspection and downstream use",
    )
    
    model_config = ConfigDict()


class PerformanceEvent(BaseModel):
    """Performance threshold breach event."""
    
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Event creation timestamp",
    )
    metric_name: str = Field(
        ...,
        description="Name of the breached metric (e.g., latency, queue_depth)",
    )
    current_value: float = Field(
        ...,
        description="The current value that breached the threshold",
    )
    threshold: float = Field(
        ...,
        description="The configured threshold value",
    )
    severity: str = Field(
        default="warning",
        description="Severity of the performance issue",
    )
    health_metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of all health metrics at the time of breach",
    )
    
    model_config = ConfigDict()

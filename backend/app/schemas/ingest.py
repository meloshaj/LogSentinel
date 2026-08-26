from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator


class LogEntry(BaseModel):
    """A single service log event emitted by a microservice."""

    timestamp: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the log event was emitted",
    )
    service_name: str | None = Field(
        default=None, description="Name of the emitting service"
    )
    service: str | None = Field(
        default=None, description="Name of the emitting service (alias)"
    )
    level: str = Field(default="info", description="Log severity")
    message: str = Field(..., min_length=1, description="The log message payload")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional structured metadata"
    )
    raw: str | None = Field(default=None, description="Raw log line if available")
    created_at: datetime | None = Field(default=None, description="Creation timestamp")

    def model_post_init(self, context: Any) -> None:
        if not self.service_name and self.service:
            self.service_name = self.service
        elif not self.service and self.service_name:
            self.service = self.service_name
        if self.created_at and not self.timestamp:
            self.timestamp = self.created_at


class IngestPayload(BaseModel):
    """Generic payload accepted by the ingestion gateway."""

    source: str = Field(
        default="unknown", min_length=1, description="Origin of the payload"
    )
    environment: str = Field(
        default="development", min_length=1, description="Runtime environment"
    )
    logs: list[LogEntry] = Field(..., min_length=1, description="A batch of log events")
    correlation_id: str | None = Field(
        default=None, description="Optional request correlation identifier"
    )


class IngestResponse(BaseModel):
    message: str = Field(..., description="Status message")
    accepted: bool = Field(..., description="Whether the payload was accepted")
    queue_size: int = Field(..., description="Current ingestion queue depth")


class BulkLogEntry(BaseModel):
    """A single log entry within a bulk ingestion payload."""

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the log event was emitted",
    )
    service_name: str | None = Field(None, description="Name of the emitting service")
    level: str = Field(default="INFO", description="Log severity")
    message: str = Field(..., min_length=1, description="The log message payload")
    trace_id: str | None = Field(None, description="Distributed trace identifier")
    span_id: str | None = Field(None, description="Current span identifier")
    parent_span_id: str | None = Field(None, description="Parent span identifier")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional structured metadata"
    )
    raw: str | None = Field(default=None, description="Raw log line if available")

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, v: Any) -> str:
        if not isinstance(v, str):
            v = str(v)
        v = v.strip().upper()
        mapping = {
            "WARN": "WARN",
            "WARNING": "WARN",
            "ERR": "ERROR",
            "ERROR": "ERROR",
            "CRIT": "CRITICAL",
            "CRITICAL": "CRITICAL",
            "FATAL": "CRITICAL",
            "DBG": "DEBUG",
            "DEBUG": "DEBUG",
            "INF": "INFO",
            "INFO": "INFO",
        }
        return mapping.get(v, "INFO")

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, v: Any) -> Any:
        if isinstance(v, (int, float)):
            if v > 1e11:
                return datetime.fromtimestamp(v / 1000.0, tz=timezone.utc)
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v


class BulkIngestPayload(BaseModel):
    """Payload for standard JSON array ingestion."""

    logs: list[BulkLogEntry] = Field(..., min_length=1, max_length=5000)


class BulkIngestResponse(BaseModel):
    status: str = Field(..., description="Status message, typically 'accepted'")
    ingested_count: int = Field(..., description="Number of logs successfully ingested")
    stream_id_last: str | None = Field(
        None, description="Redis stream ID of the last inserted message"
    )
    dropped_count: int = Field(
        0, description="Number of logs dropped due to malformed data (NDJSON)"
    )

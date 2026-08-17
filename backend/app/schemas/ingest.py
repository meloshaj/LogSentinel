from datetime import datetime, timezone
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, field_validator


class BulkLogEntry(BaseModel):
    """A single log entry within a bulk ingestion payload."""

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the log event was emitted",
    )
    service_name: Optional[str] = Field(
        None, description="Name of the emitting service"
    )
    level: str = Field(default="INFO", description="Log severity")
    message: str = Field(..., min_length=1, description="The log message payload")
    trace_id: Optional[str] = Field(None, description="Distributed trace identifier")
    span_id: Optional[str] = Field(None, description="Current span identifier")
    parent_span_id: Optional[str] = Field(None, description="Parent span identifier")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional structured metadata"
    )
    raw: Optional[str] = Field(default=None, description="Raw log line if available")

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
            # Handle unix timestamps
            # If the timestamp is very large, it might be milliseconds
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
    stream_id_last: Optional[str] = Field(None, description="Redis stream ID of the last inserted message")
    dropped_count: int = Field(0, description="Number of logs dropped due to malformed data (NDJSON)")

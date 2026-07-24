"""REST response schemas for graph topology and blast-radius retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .blast_radius import BlastRadiusResult


class TopologyResponse(BaseModel):
    generated_at: str | None
    node_count: int = Field(..., ge=0)
    edge_count: int = Field(..., ge=0)
    transaction_count: int = Field(..., ge=0)
    direction: str = Field(default="caller_to_callee")
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(validate_assignment=True)


class BlastRadiusRetrievalResponse(BaseModel):
    tracking_loop_id: int
    analysis_available: bool
    blast_radius: BlastRadiusResult | None
    suspected_root_service: str | None = None
    root_cause_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    graph_analysis_version: str | None = None
    triggered_at: datetime | None = None

    model_config = ConfigDict(validate_assignment=True)

    @field_validator("triggered_at")
    @classmethod
    def _normalize_triggered_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

"""Structured output models for graph pathway scoring and blast radius analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ImpactClassification = Literal["root", "direct", "indirect"]


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sorted_unique(values: list[str] | set[str] | tuple[str, ...] | None) -> list[str]:
    if values is None:
        return []
    return sorted({value for value in values if isinstance(value, str) and value})


class ServiceAnomalyEvidence(BaseModel):
    """Normalized anomaly evidence for one service, independent of ORM records."""

    service_name: str = Field(..., min_length=1)
    anomaly_score: float = Field(..., ge=0.0, le=1.0)
    severity_score: float = Field(..., ge=0.0, le=1.0)
    observed_at: datetime
    correlation_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    error_count: int | None = Field(default=None, ge=0)
    warning_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(validate_assignment=True)

    @field_validator("service_name")
    @classmethod
    def _clean_service_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("service_name must not be blank")
        return cleaned

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @field_validator("correlation_ids", "event_ids", mode="before")
    @classmethod
    def _normalize_ids(cls, value: Any) -> list[str]:
        return _sorted_unique(value)


class PathwayComponentScores(BaseModel):
    """Normalized score components used to calculate a pathway score."""

    root_anomaly_score: float = Field(..., ge=0.0, le=1.0)
    affected_service_anomaly_score: float = Field(..., ge=0.0, le=1.0)
    temporal_proximity: float = Field(..., ge=0.0, le=1.0)
    trace_overlap: float = Field(..., ge=0.0, le=1.0)
    edge_strength: float = Field(..., ge=0.0, le=1.0)
    hop_proximity: float = Field(..., ge=0.0, le=1.0)
    symptom_consistency: float | None = Field(default=None, ge=0.0, le=1.0)

    model_config = ConfigDict(validate_assignment=True)


class PathwayScore(BaseModel):
    """Score for one candidate root explaining one affected anomalous service."""

    candidate_root_service: str
    affected_service: str
    dependency_path: list[str]
    propagation_path: list[str]
    hop_count: int = Field(..., ge=0)
    component_scores: PathwayComponentScores
    final_score: float = Field(..., ge=0.0, le=1.0)
    supporting_correlation_ids: list[str] = Field(default_factory=list)
    supporting_event_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    model_config = ConfigDict(validate_assignment=True)

    @field_validator("supporting_correlation_ids", "supporting_event_ids", mode="before")
    @classmethod
    def _normalize_ids(cls, value: Any) -> list[str]:
        return _sorted_unique(value)


class RootCauseCandidate(BaseModel):
    """Aggregated score for a possible root-cause service."""

    service_name: str
    root_cause_score: float = Field(..., ge=0.0, le=1.0)
    explained_service_count: int = Field(..., ge=0)
    total_anomalous_services_considered: int = Field(..., ge=0)
    coverage_ratio: float = Field(..., ge=0.0, le=1.0)
    supporting_pathways: list[PathwayScore] = Field(default_factory=list)
    supporting_event_ids: list[str] = Field(default_factory=list)
    supporting_correlation_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(validate_assignment=True)

    @field_validator("supporting_correlation_ids", "supporting_event_ids", mode="before")
    @classmethod
    def _normalize_ids(cls, value: Any) -> list[str]:
        return _sorted_unique(value)


class BlastRadiusNode(BaseModel):
    """One service in the potential failure blast radius."""

    service_name: str
    hop_distance: int = Field(..., ge=0)
    impact_classification: ImpactClassification
    dependency_path: list[str]
    propagation_path: list[str]
    impact_score: float = Field(..., ge=0.0, le=1.0)
    edge_strength_score: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(validate_assignment=True)


class BlastRadiusResult(BaseModel):
    """Complete graph-scoring result, ready for later persistence or transport."""

    suspected_root_service: str | None
    root_cause_score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    ranked_root_cause_candidates: list[RootCauseCandidate] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    scored_propagation_pathways: list[PathwayScore] = Field(default_factory=list)
    blast_radius: list[BlastRadiusNode] = Field(default_factory=list)
    directly_affected_service_count: int = Field(..., ge=0)
    indirectly_affected_service_count: int = Field(..., ge=0)
    total_blast_radius_services: int = Field(..., ge=0)
    aggregate_blast_radius_score: float = Field(..., ge=0.0, le=1.0)
    supporting_event_ids: list[str] = Field(default_factory=list)
    supporting_correlation_ids: list[str] = Field(default_factory=list)
    calculated_at: datetime
    algorithm_version: str

    model_config = ConfigDict(validate_assignment=True)

    @field_validator("affected_services", "supporting_correlation_ids", "supporting_event_ids", mode="before")
    @classmethod
    def _normalize_ids(cls, value: Any) -> list[str]:
        return _sorted_unique(value)

    @field_validator("calculated_at")
    @classmethod
    def _normalize_calculated_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

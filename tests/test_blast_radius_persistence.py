from __future__ import annotations

from datetime import datetime, timezone

from backend.app.core.orm import TrackingLoopRecord
from backend.app.repositories.tracking_repository import tracking_loops_table
from backend.app.schemas.blast_radius import (
    BlastRadiusNode,
    BlastRadiusResult,
    PathwayComponentScores,
    PathwayScore,
    RootCauseCandidate,
)


BASE_TIME = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)


def populated_result() -> BlastRadiusResult:
    components = PathwayComponentScores(
        root_anomaly_score=0.9,
        affected_service_anomaly_score=0.8,
        temporal_proximity=0.7,
        trace_overlap=1.0,
        edge_strength=0.6,
        hop_proximity=0.75,
    )
    pathway = PathwayScore(
        candidate_root_service="db",
        affected_service="api",
        dependency_path=["api", "db"],
        propagation_path=["db", "api"],
        hop_count=1,
        component_scores=components,
        final_score=0.82,
        supporting_correlation_ids=["trace-1"],
        supporting_event_ids=["anomaly:1"],
        reasons=["test"],
    )
    candidate = RootCauseCandidate(
        service_name="db",
        root_cause_score=0.84,
        explained_service_count=2,
        total_anomalous_services_considered=2,
        coverage_ratio=1.0,
        supporting_pathways=[pathway],
        supporting_event_ids=["anomaly:1"],
        supporting_correlation_ids=["trace-1"],
    )
    return BlastRadiusResult(
        suspected_root_service="db",
        root_cause_score=0.84,
        confidence=0.73,
        ranked_root_cause_candidates=[candidate],
        affected_services=["api", "db"],
        scored_propagation_pathways=[pathway],
        blast_radius=[
            BlastRadiusNode(
                service_name="db",
                hop_distance=0,
                impact_classification="root",
                dependency_path=["db"],
                propagation_path=["db"],
                impact_score=0.84,
                edge_strength_score=1.0,
                supporting_evidence={"event_ids": ["anomaly:1"]},
            )
        ],
        directly_affected_service_count=1,
        indirectly_affected_service_count=0,
        total_blast_radius_services=2,
        aggregate_blast_radius_score=0.72,
        supporting_event_ids=["anomaly:1"],
        supporting_correlation_ids=["trace-1"],
        calculated_at=BASE_TIME,
        algorithm_version="test-v1",
    )


def test_blast_radius_result_serializes_for_jsonb() -> None:
    payload = populated_result().model_dump(mode="json")

    assert payload["calculated_at"] == "2026-07-24T10:00:00Z"
    assert isinstance(payload["supporting_correlation_ids"], list)
    assert payload["blast_radius"][0]["impact_classification"] == "root"
    assert not any(isinstance(value, set) for value in payload.values())


def test_tracking_loop_jsonb_field_exists_on_table_and_orm() -> None:
    assert "blast_radius" in tracking_loops_table.c
    assert TrackingLoopRecord.__table__.c.blast_radius.nullable is True


def test_migration_is_additive_and_fresh_install_sql_contains_nullable_field() -> None:
    migration = open(
        "scripts/migrations/20260724_add_tracking_loop_blast_radius.sql",
        encoding="utf-8",
    ).read().lower()
    init_sql = open("scripts/init.sql", encoding="utf-8").read().lower()

    assert "alter table tracking_loops" in migration
    assert "add column if not exists blast_radius jsonb null" in migration
    assert "drop table" not in migration
    assert "create table if not exists tracking_loops" in init_sql
    assert "blast_radius    jsonb           null" in init_sql

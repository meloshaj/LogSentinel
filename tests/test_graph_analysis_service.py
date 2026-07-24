from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import networkx as nx
import pytest

from backend.app.core.settings import GraphScoringSettings
from backend.app.models import FeatureVector
from backend.app.schemas.blast_radius import BlastRadiusResult
from backend.app.services.graph_analysis_service import (
    GraphAnalysisService,
    normalize_anomaly_score,
    severity_to_score,
)


BASE_TIME = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)


def feature_vector(
    *,
    window_id: str = "window-current",
    prediction: dict[str, Any] | None = None,
    services: dict[str, int] | None = None,
) -> FeatureVector:
    return FeatureVector(
        window_id=window_id,
        timestamp=BASE_TIME,
        window_start=BASE_TIME - timedelta(seconds=60),
        window_end=BASE_TIME,
        log_count=sum((services or {"api": 1}).values()),
        unique_templates=2,
        error_count=2,
        warning_count=1,
        service_distribution=services or {"api": 1},
        template_frequencies={},
        anomaly_prediction=prediction
        or {
            "is_anomaly": True,
            "anomaly_score": -0.4,
            "severity": "high",
            "model_version": "isolation_forest_v1",
        },
        features={"dominant_service": "api"},
    )


class FakeTopology:
    def __init__(self, graph: nx.DiGraph) -> None:
        self.graph = graph
        self.get_graph_copy_calls = 0

    def get_graph_copy(self) -> nx.DiGraph:
        self.get_graph_copy_calls += 1
        return self.graph.copy(as_view=False)


class FakeFeatureRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[dict[str, Any]] = []

    async def get_recent_anomaly_contexts(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return self.rows


class FakeLogRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[dict[str, Any]] = []

    async def get_recent_correlation_evidence(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return self.rows


class RecordingScorer:
    def __init__(self, result: BlastRadiusResult | None = None) -> None:
        self.calls: list[tuple[nx.DiGraph, list[Any], datetime | None]] = []
        self.result = result

    def score(
        self,
        graph: nx.DiGraph,
        evidence: list[Any],
        *,
        calculated_at: datetime | None = None,
    ) -> BlastRadiusResult:
        self.calls.append((graph, evidence, calculated_at))
        return self.result or BlastRadiusResult(
            suspected_root_service=evidence[0].service_name,
            root_cause_score=0.7,
            confidence=0.6,
            ranked_root_cause_candidates=[],
            affected_services=[item.service_name for item in evidence],
            scored_propagation_pathways=[],
            blast_radius=[],
            directly_affected_service_count=0,
            indirectly_affected_service_count=0,
            total_blast_radius_services=0,
            aggregate_blast_radius_score=0.0,
            supporting_event_ids=[],
            supporting_correlation_ids=[],
            calculated_at=calculated_at or BASE_TIME,
            algorithm_version="test-v1",
        )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("normal", 0.0),
        ("info", 0.0),
        ("low", 0.25),
        ("medium", 0.60),
        ("high", 0.85),
        ("critical", 1.0),
        ("HIGH", 0.85),
    ],
)
def test_severity_normalization_for_real_labels(label: str, expected: float) -> None:
    assert severity_to_score(label) == expected


def test_unknown_severity_uses_safe_fallback() -> None:
    assert severity_to_score("unexpected") == 0.5
    assert severity_to_score(None) == 0.5


def test_anomaly_score_normalization_for_bounded_and_isolation_forest_scores() -> None:
    assert normalize_anomaly_score(0.8) == 0.8
    assert normalize_anomaly_score(-0.2) == pytest.approx(1.0 - __import__("math").exp(-0.8))
    assert normalize_anomaly_score(-1.0) > normalize_anomaly_score(-0.2)
    assert normalize_anomaly_score(3.0) == pytest.approx(0.75)
    assert normalize_anomaly_score(0.8, is_anomaly=False) == 0.0
    assert normalize_anomaly_score(0.8, raw_score=0.8, is_anomaly=False) == 0.0
    assert normalize_anomaly_score(0.2, raw_score=-0.5, is_anomaly=True) == pytest.approx(
        1.0 - __import__("math").exp(-2.0)
    )
    assert normalize_anomaly_score(None) == 0.0


def test_evidence_aggregation_deduplicates_correlation_ids_and_services() -> None:
    service = GraphAnalysisService(
        topology_pipeline=FakeTopology(nx.DiGraph()),  # type: ignore[arg-type]
        feature_repository=FakeFeatureRepository(),  # type: ignore[arg-type]
        log_repository=FakeLogRepository(
            [
                {
                    "timestamp": BASE_TIME,
                    "service": "api",
                    "level": "error",
                    "correlation_id": "trace-1",
                },
                {
                    "timestamp": BASE_TIME,
                    "service": "api",
                    "level": "warning",
                    "correlation_id": "trace-1",
                },
                {
                    "timestamp": BASE_TIME,
                    "service": "api",
                    "level": "info",
                    "correlation_id": None,
                },
            ]
        ),  # type: ignore[arg-type]
        settings=GraphScoringSettings(max_log_records=10),
    )
    contexts = [
        {
            "anomaly_event_id": 2,
            "window_id": "w-2",
            "score": -0.5,
            "severity": "medium",
            "anomaly_created_at": BASE_TIME,
            "feature_vector": {"service_distribution": {"api": 3}},
        },
        {
            "anomaly_event_id": 1,
            "window_id": "w-1",
            "score": -0.1,
            "severity": "low",
            "anomaly_created_at": BASE_TIME - timedelta(seconds=30),
            "feature_vector": {"service_distribution": {"api": 1}},
        },
    ]

    evidence = asyncio.run(
        service.build_evidence(
            contexts=contexts,
            start_time=BASE_TIME - timedelta(seconds=180),
            end_time=BASE_TIME,
        )
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.service_name == "api"
    assert item.correlation_ids == ["trace-1"]
    assert item.event_ids == ["anomaly:1", "anomaly:2"]
    assert item.error_count == 1
    assert item.warning_count == 1
    assert item.observed_at == BASE_TIME - timedelta(seconds=30)


def test_evidence_query_is_anchored_to_context_correlation_ids() -> None:
    log_repo = FakeLogRepository()
    service = GraphAnalysisService(
        topology_pipeline=FakeTopology(nx.DiGraph()),  # type: ignore[arg-type]
        feature_repository=FakeFeatureRepository(),  # type: ignore[arg-type]
        log_repository=log_repo,  # type: ignore[arg-type]
        settings=GraphScoringSettings(max_log_records=10),
    )

    asyncio.run(
        service.build_evidence(
            contexts=[
                {
                    "anomaly_event_id": 1,
                    "window_id": "w-1",
                    "score": 0.9,
                    "severity": "high",
                    "anomaly_created_at": BASE_TIME,
                    "details": {"correlation_id": "incident-a"},
                    "feature_vector": {
                        "service_distribution": {
                            "api-gateway": 1,
                            "auth-service": 1,
                        }
                    },
                }
            ],
            start_time=BASE_TIME - timedelta(seconds=180),
            end_time=BASE_TIME,
        )
    )

    assert log_repo.calls[0]["correlation_ids"] == ["incident-a"]
    assert log_repo.calls[0]["services"] == ["api-gateway", "auth-service"]


def test_analyze_uses_bounded_queries_and_invokes_scorer_with_graph_copy() -> None:
    graph = nx.DiGraph()
    graph.add_edge("api", "db", transition_count=3)
    topology = FakeTopology(graph)
    feature_repo = FakeFeatureRepository()
    log_repo = FakeLogRepository()
    scorer = RecordingScorer()
    service = GraphAnalysisService(
        topology_pipeline=topology,  # type: ignore[arg-type]
        feature_repository=feature_repo,  # type: ignore[arg-type]
        log_repository=log_repo,  # type: ignore[arg-type]
        scorer=scorer,  # type: ignore[arg-type]
        settings=GraphScoringSettings(
            lookback_seconds=120,
            max_anomaly_events=7,
            max_log_records=9,
        ),
    )

    result = asyncio.run(
        service.analyze_anomaly(
            feature_vector=feature_vector(services={"api": 2}),
            calculated_at=BASE_TIME,
        )
    )

    assert result is not None
    assert topology.get_graph_copy_calls == 1
    assert feature_repo.calls[0]["start_time"] == BASE_TIME - timedelta(seconds=120)
    assert feature_repo.calls[0]["end_time"] == BASE_TIME
    assert feature_repo.calls[0]["limit"] == 7
    assert log_repo.calls[0]["limit"] == 9
    scorer_graph = scorer.calls[0][0]
    scorer_graph.add_node("mutated")
    assert "mutated" not in graph.nodes


def test_empty_graph_returns_no_result_without_queries_or_scorer() -> None:
    feature_repo = FakeFeatureRepository()
    log_repo = FakeLogRepository()
    scorer = RecordingScorer()
    service = GraphAnalysisService(
        topology_pipeline=FakeTopology(nx.DiGraph()),  # type: ignore[arg-type]
        feature_repository=feature_repo,  # type: ignore[arg-type]
        log_repository=log_repo,  # type: ignore[arg-type]
        scorer=scorer,  # type: ignore[arg-type]
    )

    result = asyncio.run(service.analyze_anomaly(feature_vector=feature_vector()))

    assert result is None
    assert feature_repo.calls == []
    assert log_repo.calls == []
    assert scorer.calls == []


def test_evidence_ordering_is_deterministic() -> None:
    service = GraphAnalysisService(
        topology_pipeline=FakeTopology(nx.DiGraph()),  # type: ignore[arg-type]
        feature_repository=FakeFeatureRepository(),  # type: ignore[arg-type]
        log_repository=FakeLogRepository(),  # type: ignore[arg-type]
    )
    contexts = [
        {
            "window_id": "w-b",
            "score": -0.3,
            "severity": "high",
            "anomaly_created_at": BASE_TIME,
            "feature_vector": {"service_distribution": {"beta": 1}},
        },
        {
            "window_id": "w-a",
            "score": -0.3,
            "severity": "high",
            "anomaly_created_at": BASE_TIME,
            "feature_vector": {"service_distribution": {"alpha": 1}},
        },
    ]

    first = asyncio.run(
        service.build_evidence(
            contexts=contexts,
            start_time=BASE_TIME - timedelta(seconds=60),
            end_time=BASE_TIME,
        )
    )
    second = asyncio.run(
        service.build_evidence(
            contexts=list(reversed(contexts)),
            start_time=BASE_TIME - timedelta(seconds=60),
            end_time=BASE_TIME,
        )
    )

    assert [item.model_dump(mode="json") for item in first] == [
        item.model_dump(mode="json") for item in second
    ]

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import networkx as nx
import pytest

from backend.app.schemas.blast_radius import ServiceAnomalyEvidence
from backend.app.services.graph_scorer import (
    DynamicGraphPathwayScorer,
    DynamicGraphScorerConfig,
)


BASE_TIME = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)


def evidence(
    service: str,
    *,
    seconds: int = 0,
    anomaly_score: float = 0.8,
    severity_score: float = 0.8,
    correlation_ids: list[str] | None = None,
    event_ids: list[str] | None = None,
) -> ServiceAnomalyEvidence:
    return ServiceAnomalyEvidence(
        service_name=service,
        anomaly_score=anomaly_score,
        severity_score=severity_score,
        observed_at=BASE_TIME + timedelta(seconds=seconds),
        correlation_ids=correlation_ids or [],
        event_ids=event_ids or [f"event-{service}"],
    )


def ecommerce_graph() -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_edge("api-gateway", "order-service", transition_count=10)
    graph.add_edge("order-service", "inventory-db", transition_count=10)
    graph.add_edge("inventory-db", "storage-engine", transition_count=10)
    return graph


def scorer(**kwargs: Any) -> DynamicGraphPathwayScorer:
    return DynamicGraphPathwayScorer(DynamicGraphScorerConfig(**kwargs))


def assert_between_zero_and_one(value: float) -> None:
    assert 0.0 <= value <= 1.0


def test_direction_semantics_rank_pathway_and_blast_radius_predecessors() -> None:
    graph = ecommerce_graph()
    result = scorer(minimum_blast_radius_impact_threshold=0.0).score(
        graph,
        [
            evidence(
                "inventory-db",
                seconds=0,
                anomaly_score=0.95,
                severity_score=1.0,
                correlation_ids=["trace-1"],
            ),
            evidence(
                "order-service",
                seconds=20,
                anomaly_score=0.75,
                severity_score=0.8,
                correlation_ids=["trace-1"],
            ),
            evidence(
                "api-gateway",
                seconds=40,
                anomaly_score=0.70,
                severity_score=0.75,
                correlation_ids=["trace-1"],
            ),
        ],
        calculated_at=BASE_TIME,
    )

    assert result.suspected_root_service == "inventory-db"
    api_pathway = next(
        pathway
        for pathway in result.scored_propagation_pathways
        if pathway.affected_service == "api-gateway"
    )
    assert api_pathway.dependency_path == [
        "api-gateway",
        "order-service",
        "inventory-db",
    ]
    assert api_pathway.propagation_path == [
        "inventory-db",
        "order-service",
        "api-gateway",
    ]
    assert {node.service_name for node in result.blast_radius} == {
        "inventory-db",
        "order-service",
        "api-gateway",
    }
    assert "storage-engine" not in {node.service_name for node in result.blast_radius}


def test_root_ranking_prefers_early_correlated_inventory_database() -> None:
    graph = ecommerce_graph()
    result = scorer(minimum_blast_radius_impact_threshold=0.0).score(
        graph,
        [
            evidence(
                "api-gateway",
                seconds=60,
                anomaly_score=0.65,
                severity_score=0.65,
                correlation_ids=["checkout-123"],
            ),
            evidence(
                "inventory-db",
                seconds=0,
                anomaly_score=1.0,
                severity_score=1.0,
                correlation_ids=["checkout-123"],
            ),
            evidence(
                "order-service",
                seconds=30,
                anomaly_score=0.75,
                severity_score=0.8,
                correlation_ids=["checkout-123"],
            ),
        ],
        calculated_at=BASE_TIME,
    )

    assert result.ranked_root_cause_candidates[0].service_name == "inventory-db"
    assert result.ranked_root_cause_candidates[0].explained_service_count == 3
    assert result.ranked_root_cause_candidates[0].coverage_ratio == 1.0
    assert result.confidence != result.root_cause_score


def test_trace_overlap_jaccard_cases() -> None:
    subject = scorer()

    assert subject.trace_overlap(["a", "b"], ["a", "b"]) == 1.0
    assert subject.trace_overlap(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)
    assert subject.trace_overlap(["a"], ["b"]) == 0.0
    assert subject.trace_overlap([], []) == 0.0
    assert subject.trace_overlap(["a"], []) == 0.0


def test_temporal_decay_cases() -> None:
    subject = scorer(temporal_decay_seconds=10.0, future_evidence_penalty=0.05)
    summaries = subject._summarize_evidence(
        [
            evidence("root", seconds=0),
            evidence("same", seconds=0),
            evidence("near", seconds=10),
            evidence("far", seconds=100),
            evidence("before-root", seconds=-10),
        ]
    )

    assert subject.temporal_proximity(summaries["root"], summaries["same"]) == 1.0
    assert subject.temporal_proximity(summaries["root"], summaries["near"]) == pytest.approx(
        math.exp(-1)
    )
    assert subject.temporal_proximity(summaries["root"], summaries["far"]) == pytest.approx(
        math.exp(-10)
    )
    assert subject.temporal_proximity(
        summaries["root"], summaries["before-root"]
    ) == pytest.approx(0.05 * math.exp(-1))
    assert subject.temporal_proximity(None, summaries["near"]) == 0.0


def test_future_root_temporal_penalty_decays_monotonically() -> None:
    subject = scorer(temporal_decay_seconds=10.0, future_evidence_penalty=0.05)
    summaries = subject._summarize_evidence(
        [
            evidence("symptom", seconds=0),
            evidence("future-1", seconds=1),
            evidence("future-5", seconds=5),
            evidence("future-60", seconds=60),
        ]
    )

    after_1 = subject.temporal_proximity(summaries["future-1"], summaries["symptom"])
    after_5 = subject.temporal_proximity(summaries["future-5"], summaries["symptom"])
    after_60 = subject.temporal_proximity(summaries["future-60"], summaries["symptom"])

    assert after_5 < after_1
    assert after_60 < after_5
    for value in [after_1, after_5, after_60]:
        assert_between_zero_and_one(value)
        assert value <= subject.config.future_evidence_penalty


def test_hop_decay_reduces_pathway_component_by_hops() -> None:
    graph = ecommerce_graph()
    subject = scorer(hop_decay=0.5, minimum_pathway_score_threshold=0.0)
    summaries = subject._summarize_evidence(
        [
            evidence("inventory-db", seconds=0, correlation_ids=["t"]),
            evidence("order-service", seconds=1, correlation_ids=["t"]),
            evidence("api-gateway", seconds=2, correlation_ids=["t"]),
        ]
    )

    direct = subject.score_pathway(graph, "inventory-db", "order-service", summaries)
    indirect = subject.score_pathway(graph, "inventory-db", "api-gateway", summaries)

    assert direct is not None
    assert indirect is not None
    assert direct.component_scores.hop_proximity == pytest.approx(0.5)
    assert indirect.component_scores.hop_proximity == pytest.approx(0.25)


def test_edge_strength_normalization_geometric_mean_and_fallbacks() -> None:
    graph = nx.DiGraph()
    graph.add_edge("api", "strong", transition_count=10)
    graph.add_edge("api", "weak", transition_count=5)
    graph.add_edge("weak", "db", transition_count="bad")
    graph.add_edge("strong", "db", transition_count=float("nan"))
    subject = scorer(neutral_edge_strength=0.4)

    assert subject.edge_strength(graph, "api", "strong") == 1.0
    assert subject.edge_strength(graph, "api", "weak") == 0.5
    assert subject.edge_strength(graph, "weak", "db") == 0.4
    assert subject.edge_strength(graph, "strong", "db") == 0.4
    assert subject.path_edge_strength(graph, ["api", "weak", "db"]) == pytest.approx(
        math.sqrt(0.5 * 0.4)
    )


def test_candidate_path_selection_tie_breaking_depth_disconnected_and_isolated() -> None:
    graph = nx.DiGraph()
    graph.add_edge("api", "order", transition_count=10)
    graph.add_edge("order", "db", transition_count=10)
    graph.add_edge("api", "payment", transition_count=10)
    graph.add_edge("payment", "db", transition_count=10)
    graph.add_node("isolated")

    subject = scorer(maximum_depth=2)
    assert subject.find_best_dependency_path(graph, "api", "db") == [
        "api",
        "order",
        "db",
    ]
    assert scorer(maximum_depth=1).find_best_dependency_path(graph, "api", "db") is None
    assert subject.find_best_dependency_path(graph, "isolated", "db") is None

    result = subject.score(
        graph,
        [evidence("isolated", anomaly_score=0.9, severity_score=0.9)],
        calculated_at=BASE_TIME,
    )
    assert result.suspected_root_service == "isolated"
    assert result.blast_radius[0].impact_classification == "root"


def test_blast_radius_classification_and_impact_threshold_filtering() -> None:
    graph = ecommerce_graph()
    result = scorer(
        hop_decay=0.5,
        minimum_blast_radius_impact_threshold=0.25,
    ).score(
        graph,
        [
            evidence("inventory-db", seconds=0, anomaly_score=1.0, severity_score=1.0),
            evidence("api-gateway", seconds=10, anomaly_score=0.8, severity_score=0.8),
        ],
        calculated_at=BASE_TIME,
    )

    classifications = {
        node.service_name: node.impact_classification for node in result.blast_radius
    }
    assert classifications["inventory-db"] == "root"
    assert classifications["order-service"] == "direct"
    assert "api-gateway" not in classifications
    assert result.directly_affected_service_count == 1
    assert result.indirectly_affected_service_count == 0


def test_cycles_terminate_without_duplicate_affected_services() -> None:
    graph = nx.DiGraph()
    graph.add_edge("a", "b", transition_count=5)
    graph.add_edge("b", "c", transition_count=5)
    graph.add_edge("c", "a", transition_count=5)

    result = scorer(maximum_depth=4, minimum_blast_radius_impact_threshold=0.0).score(
        graph,
        [
            evidence("c", seconds=0, anomaly_score=0.9, severity_score=0.9),
            evidence("a", seconds=5, anomaly_score=0.8, severity_score=0.8),
        ],
        calculated_at=BASE_TIME,
    )

    names = [node.service_name for node in result.blast_radius]
    assert sorted(names) == ["a", "b", "c"]
    assert len(names) == len(set(names))


def test_empty_inputs_and_absent_services_are_documented() -> None:
    subject = scorer()

    empty = subject.score(nx.DiGraph(), [], calculated_at=BASE_TIME)
    assert empty.suspected_root_service is None
    assert empty.total_blast_radius_services == 0

    absent = subject.score(
        nx.DiGraph(),
        [evidence("missing-service", anomaly_score=0.9, severity_score=0.8)],
        calculated_at=BASE_TIME,
    )
    assert absent.suspected_root_service == "missing-service"
    assert absent.blast_radius[0].dependency_path == ["missing-service"]


def test_numerical_bounds_for_all_scores() -> None:
    result = scorer(minimum_blast_radius_impact_threshold=0.0).score(
        ecommerce_graph(),
        [
            evidence("inventory-db", seconds=0, anomaly_score=1.0, severity_score=1.0),
            evidence("order-service", seconds=20, anomaly_score=0.8, severity_score=0.7),
            evidence("api-gateway", seconds=40, anomaly_score=0.7, severity_score=0.6),
        ],
        calculated_at=BASE_TIME,
    )

    assert_between_zero_and_one(result.root_cause_score)
    assert_between_zero_and_one(result.confidence)
    assert_between_zero_and_one(result.aggregate_blast_radius_score)
    for candidate in result.ranked_root_cause_candidates:
        assert_between_zero_and_one(candidate.root_cause_score)
        assert_between_zero_and_one(candidate.coverage_ratio)
    for pathway in result.scored_propagation_pathways:
        assert_between_zero_and_one(pathway.final_score)
        for value in pathway.component_scores.model_dump().values():
            if value is not None:
                assert_between_zero_and_one(value)
    for node in result.blast_radius:
        assert_between_zero_and_one(node.impact_score)
        assert_between_zero_and_one(node.edge_strength_score)


def test_deterministic_serialized_output_with_different_insertion_orders() -> None:
    graph_one = nx.DiGraph()
    graph_one.add_edge("api-gateway", "order-service", transition_count=10)
    graph_one.add_edge("order-service", "inventory-db", transition_count=10)

    graph_two = nx.DiGraph()
    graph_two.add_edge("order-service", "inventory-db", transition_count=10)
    graph_two.add_edge("api-gateway", "order-service", transition_count=10)

    evidence_one = [
        evidence("inventory-db", seconds=0, correlation_ids=["trace"]),
        evidence("order-service", seconds=10, correlation_ids=["trace"]),
        evidence("api-gateway", seconds=20, correlation_ids=["trace"]),
    ]
    evidence_two = list(reversed(evidence_one))
    subject = scorer(minimum_blast_radius_impact_threshold=0.0)

    first = subject.score(graph_one, evidence_one, calculated_at=BASE_TIME).model_dump(
        mode="json"
    )
    second = subject.score(graph_two, evidence_two, calculated_at=BASE_TIME).model_dump(
        mode="json"
    )

    assert first == second

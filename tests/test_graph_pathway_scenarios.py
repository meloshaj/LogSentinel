from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import networkx as nx
import pytest

from backend.app.core.settings import GraphScoringSettings
from backend.app.models import FeatureVector
from backend.app.schemas.blast_radius import BlastRadiusResult
from backend.app.services.graph_analysis_service import GraphAnalysisService
from backend.app.services.graph_scorer import DynamicGraphPathwayScorer
from backend.app.workers.event_manager import EventManager
from backend.tools.log_generator.config import default_ecommerce_topology
from backend.tools.log_generator.scenarios import (
    AuthTokenStormScenario,
    DatabasePoolExhaustionScenario,
)
from tests.test_graph_api_routes import (
    FakeTrackingRepository as RouteTrackingRepository,
    auth_headers,
)


BASE_TIME = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)


class StaticTopologyPipeline:
    def __init__(self) -> None:
        self.graph = nx.DiGraph()
        for service in default_ecommerce_topology().services:
            self.graph.add_node(service.service_name, service=service.service_name)
            for dependency in service.dependencies:
                self.graph.add_edge(
                    service.service_name,
                    dependency,
                    transition_count=10,
                    average_delay_ms=25.0,
                )

    def get_graph_copy(self) -> nx.DiGraph:
        return self.graph.copy(as_view=False)


class ScenarioFeatureRepository:
    def __init__(self, contexts: list[dict[str, Any]]) -> None:
        self.contexts = contexts

    async def get_recent_anomaly_contexts(self, **kwargs: Any) -> list[dict[str, Any]]:
        start_time = kwargs["start_time"]
        end_time = kwargs["end_time"]
        limit = kwargs["limit"]
        rows = [
            context
            for context in self.contexts
            if start_time <= context["anomaly_created_at"] <= end_time
        ]
        return rows[:limit]


class ScenarioLogRepository:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def get_recent_correlation_evidence(self, **kwargs: Any) -> list[dict[str, Any]]:
        services = set(kwargs.get("services") or [])
        start_time = kwargs["start_time"]
        end_time = kwargs["end_time"]
        limit = kwargs["limit"]
        rows = [
            row
            for row in self.rows
            if row["service"] in services and start_time <= row["timestamp"] <= end_time
        ]
        return rows[:limit]


class CapturingTrackingRepository:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.next_id = 1

    async def persist_tracking_loop(self, **kwargs: Any) -> None:
        row_id = self.next_id
        self.next_id += 1
        self.rows[row_id] = {
            "id": row_id,
            "window_id": kwargs["window_id"],
            "anomaly_score": kwargs["anomaly_score"],
            "status": kwargs["status"],
            "blast_radius": kwargs["blast_radius"],
            "created_at": BASE_TIME,
        }

    async def get_tracking_loop_by_id(self, tracking_loop_id: int) -> dict[str, Any] | None:
        return self.rows.get(tracking_loop_id)


class CapturingBroadcaster:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def scenario_contexts(logs: list[Any], *, offset_seconds: int = 0) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for index, log in enumerate(logs):
        metadata = log.metadata
        if not (metadata.get("root_cause") or metadata.get("propagated_symptom")):
            continue
        timestamp = BASE_TIME + timedelta(seconds=offset_seconds, milliseconds=index * 100)
        is_root = bool(metadata.get("root_cause"))
        contexts.append(
            {
                "anomaly_event_id": index + 1 + (offset_seconds * 1000),
                "window_id": f"scenario-{offset_seconds}-{index}",
                "severity": "critical" if is_root else "high",
                "score": 1.0 if is_root else 0.85,
                "details": {
                    "is_anomaly": True,
                    "anomaly_score": 1.0 if is_root else 0.85,
                    "severity": "critical" if is_root else "high",
                },
                "anomaly_created_at": timestamp,
                "start_time": timestamp,
                "end_time": timestamp,
                "feature_vector": {"service_distribution": {log.service_name: 1}},
            }
        )
    return contexts


def scenario_log_rows(logs: list[Any], *, offset_seconds: int = 0) -> list[dict[str, Any]]:
    rows = []
    for index, log in enumerate(logs):
        rows.append(
            {
                "timestamp": BASE_TIME + timedelta(seconds=offset_seconds, milliseconds=index * 100),
                "service": log.service_name,
                "level": log.level,
                "correlation_id": log.metadata.get("correlation_id"),
            }
        )
    return rows


def feature_vector_for_contexts(
    contexts: list[dict[str, Any]],
    *,
    window_id: str = "scenario-current",
    anomaly_score: float = 1.0,
) -> FeatureVector:
    services = {
        service: 1
        for context in contexts
        for service in context["feature_vector"]["service_distribution"]
    }
    return FeatureVector(
        window_id=window_id,
        timestamp=BASE_TIME,
        window_start=BASE_TIME,
        window_end=max(context["end_time"] for context in contexts),
        log_count=max(len(contexts), 1),
        unique_templates=1,
        error_count=len(contexts),
        warning_count=0,
        service_distribution=services or {"api-gateway": 1},
        template_frequencies={},
        anomaly_prediction={
            "is_anomaly": True,
            "anomaly_score": anomaly_score,
            "severity": "critical",
            "model_version": "scenario-test",
        },
    )


def analyze_contexts(
    contexts: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    lookback_seconds: int = 300,
) -> BlastRadiusResult:
    service = GraphAnalysisService(
        topology_pipeline=StaticTopologyPipeline(),  # type: ignore[arg-type]
        feature_repository=ScenarioFeatureRepository(contexts),  # type: ignore[arg-type]
        log_repository=ScenarioLogRepository(rows),  # type: ignore[arg-type]
        scorer=DynamicGraphPathwayScorer(),
        settings=GraphScoringSettings(
            lookback_seconds=lookback_seconds,
            max_anomaly_events=100,
            max_log_records=100,
        ),
    )
    result = asyncio.run(
        service.analyze_anomaly(
            feature_vector=feature_vector_for_contexts(contexts),
            calculated_at=BASE_TIME,
        )
    )
    assert result is not None
    return result


def assert_common_invariants(result: BlastRadiusResult) -> None:
    assert result.algorithm_version
    assert 0.0 <= result.confidence <= 1.0
    for node in result.blast_radius:
        assert 0.0 <= node.impact_score <= 1.0
    dumped = result.model_dump(mode="json")
    assert BlastRadiusResult.model_validate(dumped).model_dump(mode="json") == dumped


def test_database_pool_exhaustion_scenario_identifies_inventory_db() -> None:
    scenario = DatabasePoolExhaustionScenario(seed=42)
    logs = scenario.get_step(4).logs
    contexts = scenario_contexts(logs)
    result = analyze_contexts(contexts, scenario_log_rows(logs))

    assert result.suspected_root_service == "inventory-db"
    assert result.ranked_root_cause_candidates[0].service_name == "inventory-db"
    services = {node.service_name: node for node in result.blast_radius}
    assert services["order-service"].impact_classification == "direct"
    assert services["api-gateway"].impact_classification == "indirect"
    pathway = next(
        item
        for item in result.scored_propagation_pathways
        if item.affected_service == "api-gateway"
    )
    assert pathway.dependency_path == ["api-gateway", "order-service", "inventory-db"]
    assert pathway.propagation_path == ["inventory-db", "order-service", "api-gateway"]
    assert pathway.supporting_correlation_ids
    assert_common_invariants(result)


def test_auth_token_storm_scenario_identifies_auth_service() -> None:
    scenario = AuthTokenStormScenario(seed=7)
    logs = scenario.get_step(3).logs
    contexts = scenario_contexts(logs)
    result = analyze_contexts(contexts, scenario_log_rows(logs))

    assert result.suspected_root_service == "auth-service"
    assert result.ranked_root_cause_candidates[0].service_name == "auth-service"
    assert {
        candidate.service_name for candidate in result.ranked_root_cause_candidates[:2]
    } >= {"auth-service", "api-gateway"}
    gateway = next(node for node in result.blast_radius if node.service_name == "api-gateway")
    assert gateway.impact_classification == "direct"
    pathway = next(
        item
        for item in result.scored_propagation_pathways
        if item.affected_service == "api-gateway"
    )
    assert pathway.supporting_correlation_ids
    assert_common_invariants(result)


def test_recovery_phase_old_failure_evidence_does_not_dominate_indefinitely() -> None:
    scenario = DatabasePoolExhaustionScenario(seed=42)
    failure_logs = scenario.get_step(4).logs
    recovery_logs = scenario.get_step(5).logs
    failure_contexts = scenario_contexts(failure_logs, offset_seconds=0)
    failure_rows = scenario_log_rows(failure_logs, offset_seconds=0)
    failure = analyze_contexts(failure_contexts, failure_rows, lookback_seconds=300)

    recovery_contexts = [
        {
            "anomaly_event_id": 9001,
            "window_id": "recovery-symptom",
            "severity": "low",
            "score": 0.25,
            "details": {
                "is_anomaly": True,
                "anomaly_score": 0.25,
                "severity": "low",
            },
            "anomaly_created_at": BASE_TIME + timedelta(seconds=600),
            "start_time": BASE_TIME + timedelta(seconds=600),
            "end_time": BASE_TIME + timedelta(seconds=600),
            "feature_vector": {"service_distribution": {"api-gateway": 1}},
        }
    ]
    recovery_rows = scenario_log_rows(recovery_logs, offset_seconds=600)
    recovery = analyze_contexts(
        failure_contexts + recovery_contexts,
        failure_rows + recovery_rows,
        lookback_seconds=30,
    )

    assert recovery.confidence < failure.confidence
    assert recovery.aggregate_blast_radius_score <= failure.aggregate_blast_radius_score


def test_runtime_chain_persists_broadcasts_and_rest_retrieves(monkeypatch) -> None:
    scenario = DatabasePoolExhaustionScenario(seed=42)
    logs = scenario.get_step(4).logs
    contexts = scenario_contexts(logs)
    graph_service = GraphAnalysisService(
        topology_pipeline=StaticTopologyPipeline(),  # type: ignore[arg-type]
        feature_repository=ScenarioFeatureRepository(contexts),  # type: ignore[arg-type]
        log_repository=ScenarioLogRepository(scenario_log_rows(logs)),  # type: ignore[arg-type]
        scorer=DynamicGraphPathwayScorer(),
        settings=GraphScoringSettings(max_anomaly_events=100, max_log_records=100),
    )
    tracking_repo = CapturingTrackingRepository()
    broadcaster = CapturingBroadcaster()
    manager = EventManager(
        tracking_repository=tracking_repo,  # type: ignore[arg-type]
        graph_analysis_service=graph_service,
        graph_scoring_settings=GraphScoringSettings(timeout_seconds=2.0),
        telemetry_broadcaster=broadcaster,
    )
    fv = feature_vector_for_contexts(contexts, anomaly_score=1.0)

    asyncio.run(manager._process_event(fv))

    stored = tracking_repo.rows[1]["blast_radius"]
    assert stored is not None
    assert broadcaster.events[0]["payload"]["blast_radius"] == stored

    monkeypatch.setattr(
        "backend.app.main.tracking_repository",
        RouteTrackingRepository({1: tracking_repo.rows[1]}),
    )
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setenv("INGEST_API_KEY", "test-graph-key")

    response = TestClient(app).get(
        "/api/v1/tracking-loops/1/blast-radius",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_available"] is True
    assert body["blast_radius"] == stored
    assert body["root_cause_confidence"] == stored["confidence"]
    assert body["graph_analysis_version"] == stored["algorithm_version"]

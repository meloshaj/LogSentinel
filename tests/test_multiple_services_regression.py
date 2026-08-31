"""Authoritative regression coverage for the three-service incident path."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.app.ml.feature_extractor import WindowConfig
from backend.app.models import FeatureVector
from backend.app.services.drain_parser import DrainParser
from backend.app.services.graph_analysis_service import GraphAnalysisService
from backend.app.services.graph_scorer import DynamicGraphPathwayScorer
from backend.app.services.runtime_dependency_parser import TraceObservation
from backend.app.services.topology_pipeline import NetworkXTopologyPipeline
from backend.app.workers.event_manager import EventManager
from backend.app.workers.feature_worker import FeatureExtractionWorker


def test_three_services_survive_drain_and_feature_window() -> None:
    state_path = Path.cwd() / f".drain-regression-{uuid4().hex}.bin"
    parser = DrainParser(state_path=str(state_path))
    base = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    services = ("api-gateway", "order-service", "payment-gateway")
    try:
        parsed = [
            parser.parse(
                f"{service} request failed attempt={index}",
                metadata={
                    "service": service,
                    "level": "ERROR" if index else "INFO",
                    "timestamp": base + timedelta(seconds=index),
                    "tenant_id": "tenant-a",
                    "correlation_id": "trace-3-service",
                },
            )
            for index, service in enumerate(services)
        ]
    finally:
        state_path.unlink(missing_ok=True)

    worker = FeatureExtractionWorker(
        window_config=WindowConfig(
            window_size_seconds=10,
            stride_seconds=10,
            min_logs_per_window=1,
        )
    )
    worker.add_parsed_logs(parsed)
    vectors = asyncio.run(
        worker.extract_pending_features(current_time=base + timedelta(seconds=20))
    )

    assert len(vectors) == 1
    assert vectors[0].tenant_id == "tenant-a"
    assert set(vectors[0].service_distribution) == set(services)
    assert vectors[0].features["active_services"] == 3.0


class _CapturingTrackingRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def persist_tracking_loop(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _CapturingBroadcaster:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _GraphResult:
    def __init__(self) -> None:
        self.suspected_root_service = "payment-gateway"
        self.confidence = 0.91
        self.algorithm_version = "test"

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {
            "suspected_root_service": self.suspected_root_service,
            "confidence": self.confidence,
            "algorithm_version": self.algorithm_version,
            "blast_radius": [
                {"service_name": name, "impact_classification": "direct"}
                for name in ("api-gateway", "order-service", "payment-gateway")
            ],
        }


class _AvailableGraph:
    async def analyze_anomaly(self, **kwargs: Any) -> _GraphResult:
        return _GraphResult()


def _anomalous_vector() -> FeatureVector:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    return FeatureVector(
        tenant_id="tenant-a",
        window_id="window-tenant-a",
        timestamp=now,
        window_start=now,
        window_end=now + timedelta(seconds=10),
        log_count=3,
        unique_templates=3,
        service_distribution={
            "api-gateway": 1,
            "order-service": 1,
            "payment-gateway": 1,
        },
        features={"active_services": 3.0},
        anomaly_prediction={"is_anomaly": True, "anomaly_score": 0.95},
    )


def test_event_manager_persists_three_service_graph_result_with_tenant() -> None:
    repository = _CapturingTrackingRepository()
    broadcaster = _CapturingBroadcaster()
    manager = EventManager(
        tracking_repository=repository,  # type: ignore[arg-type]
        graph_analysis_service=_AvailableGraph(),  # type: ignore[arg-type]
        telemetry_broadcaster=broadcaster,
    )

    asyncio.run(manager._process_event(_anomalous_vector()))

    assert repository.calls[0]["tenant_id"] == "tenant-a"
    assert {
        node["service_name"] for node in repository.calls[0]["blast_radius"]["blast_radius"]
    } == {"api-gateway", "order-service", "payment-gateway"}
    assert broadcaster.events[0]["payload"]["suspected_root_service"] == "payment-gateway"


def test_event_manager_graph_failure_preserves_alert_without_synthetic_root() -> None:
    repository = _CapturingTrackingRepository()
    broadcaster = _CapturingBroadcaster()
    manager = EventManager(
        tracking_repository=repository,  # type: ignore[arg-type]
        graph_analysis_service=None,
        telemetry_broadcaster=broadcaster,
    )

    asyncio.run(manager._process_event(_anomalous_vector()))

    assert repository.calls[0]["tenant_id"] == "tenant-a"
    assert repository.calls[0]["blast_radius"] is None
    payload = broadcaster.events[0]["payload"]
    assert "suspected_root_service" not in payload
    assert payload["window_id"] == "window-tenant-a"


@pytest.mark.asyncio
async def test_graph_analysis_passes_feature_tenant_to_bounded_queries() -> None:
    class FeatureRepo:
        async def get_recent_anomaly_contexts(self, **kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs["tenant_id"] == "tenant-a"
            return []

    class LogRepo:
        async def get_recent_correlation_evidence(self, **kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs["tenant_id"] == "tenant-a"
            return []

    topology = NetworkXTopologyPipeline()
    topology.add_observation(
        TraceObservation(
            canonical_transaction_id="trace-3-service",
            service="payment-gateway",
            timestamp=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
            template_id="payment-error",
        )
    )
    result = await GraphAnalysisService(
        topology_pipeline=topology,
        feature_repository=FeatureRepo(),  # type: ignore[arg-type]
        log_repository=LogRepo(),  # type: ignore[arg-type]
        scorer=DynamicGraphPathwayScorer(),
    ).analyze_anomaly(feature_vector=_anomalous_vector())

    assert result is not None
    assert result.suspected_root_service in _anomalous_vector().service_distribution

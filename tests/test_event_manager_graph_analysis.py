from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.core.settings import GraphScoringSettings
from backend.app.models import FeatureVector
from backend.app.schemas.blast_radius import BlastRadiusResult
from backend.app.workers.event_manager import EventManager


BASE_TIME = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)


def feature_vector() -> FeatureVector:
    return FeatureVector(
        window_id="window-1",
        timestamp=BASE_TIME,
        window_start=BASE_TIME,
        window_end=BASE_TIME,
        log_count=10,
        unique_templates=2,
        error_count=3,
        warning_count=1,
        service_distribution={"api": 10},
        template_frequencies={},
        anomaly_prediction={
            "is_anomaly": True,
            "anomaly_score": 0.9,
            "severity": "high",
            "model_version": "test-model",
        },
    )


def feature_vector_with_prediction(prediction: dict[str, Any]) -> FeatureVector:
    fv = feature_vector()
    fv.anomaly_prediction = prediction
    return fv


def blast_radius_result() -> BlastRadiusResult:
    return BlastRadiusResult(
        suspected_root_service="db",
        root_cause_score=0.8,
        confidence=0.7,
        ranked_root_cause_candidates=[],
        affected_services=["db", "api"],
        scored_propagation_pathways=[],
        blast_radius=[],
        directly_affected_service_count=1,
        indirectly_affected_service_count=0,
        total_blast_radius_services=2,
        aggregate_blast_radius_score=0.6,
        supporting_event_ids=["anomaly:1"],
        supporting_correlation_ids=["trace-1"],
        calculated_at=BASE_TIME,
        algorithm_version="test-graph-v1",
    )


class FakeTrackingRepository:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def persist_tracking_loop(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("temporary database failure")


class FakeBroadcaster:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class FakeGraphAnalysisService:
    def __init__(
        self,
        result: BlastRadiusResult | None = None,
        *,
        error: BaseException | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.result = result
        self.error = error
        self.delay_seconds = delay_seconds
        self.calls: list[FeatureVector] = []

    async def analyze_anomaly(
        self,
        *,
        feature_vector: FeatureVector,
        calculated_at: datetime | None = None,
    ) -> BlastRadiusResult | None:
        self.calls.append(feature_vector)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        return self.result


def test_event_manager_success_persists_and_broadcasts_blast_radius() -> None:
    repo = FakeTrackingRepository()
    broadcaster = FakeBroadcaster()
    graph_service = FakeGraphAnalysisService(blast_radius_result())
    manager = EventManager(
        tracking_repository=repo,  # type: ignore[arg-type]
        graph_analysis_service=graph_service,  # type: ignore[arg-type]
        graph_scoring_settings=GraphScoringSettings(enabled=True),
        telemetry_broadcaster=broadcaster,
    )

    fv = feature_vector()
    asyncio.run(manager._process_event(fv))

    assert len(graph_service.calls) == 1
    assert len(repo.calls) == 1
    blast_payload = repo.calls[0]["blast_radius"]
    assert blast_payload["suspected_root_service"] == "db"
    payload = broadcaster.events[0]["payload"]
    assert payload["window_id"] == "window-1"
    assert payload["anomaly_score"] == 0.9
    assert payload["severity"] == "high"
    assert payload["model_version"] == "test-model"
    assert payload["status"] == "triggered"
    assert payload["suspected_root_service"] == "db"
    assert payload["root_cause_confidence"] == 0.7
    assert payload["graph_analysis_version"] == "test-graph-v1"
    assert payload["blast_radius"] == []


@pytest.mark.parametrize(
    "graph_service",
    [
        FakeGraphAnalysisService(error=RuntimeError("scorer failed")),
        FakeGraphAnalysisService(error=OSError("repository unavailable")),
        FakeGraphAnalysisService(result=None),
    ],
)
def test_event_manager_graph_analysis_failures_preserve_original_alert(
    graph_service: FakeGraphAnalysisService,
) -> None:
    repo = FakeTrackingRepository()
    broadcaster = FakeBroadcaster()
    manager = EventManager(
        tracking_repository=repo,  # type: ignore[arg-type]
        graph_analysis_service=graph_service,  # type: ignore[arg-type]
        graph_scoring_settings=GraphScoringSettings(enabled=True),
        telemetry_broadcaster=broadcaster,
    )

    asyncio.run(manager._process_event(feature_vector()))

    assert len(repo.calls) == 1
    assert repo.calls[0]["blast_radius"] is None
    payload = broadcaster.events[0]["payload"]
    assert payload == {
        "window_id": "window-1",
        "anomaly_score": 0.9,
        "severity": "high",
        "model_version": "test-model",
        "status": "triggered",
    }


def test_event_manager_graph_analysis_timeout_preserves_processing() -> None:
    repo = FakeTrackingRepository()
    broadcaster = FakeBroadcaster()
    graph_service = FakeGraphAnalysisService(delay_seconds=0.05)
    manager = EventManager(
        tracking_repository=repo,  # type: ignore[arg-type]
        graph_analysis_service=graph_service,  # type: ignore[arg-type]
        graph_scoring_settings=GraphScoringSettings(
            enabled=True,
            timeout_seconds=0.001,
        ),
        telemetry_broadcaster=broadcaster,
    )

    asyncio.run(manager._process_event(feature_vector()))

    assert len(repo.calls) == 1
    assert repo.calls[0]["blast_radius"] is None
    assert len(broadcaster.events) == 1


def test_event_manager_disabled_graph_scoring_does_not_call_dependency() -> None:
    repo = FakeTrackingRepository()
    broadcaster = FakeBroadcaster()
    graph_service = FakeGraphAnalysisService(blast_radius_result())
    manager = EventManager(
        tracking_repository=repo,  # type: ignore[arg-type]
        graph_analysis_service=graph_service,  # type: ignore[arg-type]
        graph_scoring_settings=GraphScoringSettings(enabled=False),
        telemetry_broadcaster=broadcaster,
    )

    asyncio.run(manager._process_event(feature_vector()))

    assert graph_service.calls == []
    assert repo.calls[0]["blast_radius"] is None
    assert "blast_radius" not in broadcaster.events[0]["payload"]


def test_event_manager_cancelled_error_is_not_swallowed() -> None:
    manager = EventManager(
        tracking_repository=FakeTrackingRepository(),  # type: ignore[arg-type]
        graph_analysis_service=FakeGraphAnalysisService(error=asyncio.CancelledError()),  # type: ignore[arg-type]
        graph_scoring_settings=GraphScoringSettings(enabled=True),
        telemetry_broadcaster=FakeBroadcaster(),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager._run_graph_analysis(feature_vector()))


def test_tracking_repository_error_does_not_block_broadcast() -> None:
    repo = FakeTrackingRepository(fail=True)
    broadcaster = FakeBroadcaster()
    manager = EventManager(
        tracking_repository=repo,  # type: ignore[arg-type]
        graph_analysis_service=FakeGraphAnalysisService(None),  # type: ignore[arg-type]
        graph_scoring_settings=GraphScoringSettings(enabled=True),
        telemetry_broadcaster=broadcaster,
    )

    asyncio.run(manager._process_event(feature_vector()))

    assert len(repo.calls) == 1
    assert len(broadcaster.events) == 1


def test_event_manager_uses_authoritative_boolean_and_normalized_score() -> None:
    repo = FakeTrackingRepository()
    broadcaster = FakeBroadcaster()
    manager = EventManager(
        tracking_repository=repo,  # type: ignore[arg-type]
        graph_analysis_service=FakeGraphAnalysisService(None),  # type: ignore[arg-type]
        graph_scoring_settings=GraphScoringSettings(enabled=True),
        telemetry_broadcaster=broadcaster,
    )

    asyncio.run(
        manager._process_event(
            feature_vector_with_prediction(
                {
                    "is_anomaly": True,
                    "raw_score": -0.5,
                    "anomaly_score": 0.864665,
                    "severity": "high",
                    "model_version": "test-model",
                }
            )
        )
    )
    asyncio.run(
        manager._process_event(
            feature_vector_with_prediction(
                {
                    "is_anomaly": False,
                    "raw_score": 0.8,
                    "anomaly_score": 0.8,
                    "severity": "normal",
                    "model_version": "test-model",
                }
            )
        )
    )

    assert len(repo.calls) == 1
    assert repo.calls[0]["anomaly_score"] == pytest.approx(0.864665, abs=1e-6)
    assert len(broadcaster.events) == 1

"""End-to-End Integration Test Suite for LogSentinel Data Pipeline.

Tests the full synchronous and asynchronous processing loop:
1. Log Ingestion & Drain3 Template Mining (with parameter masking)
2. Sliding-Window Statistical & Semantic Feature Extraction
3. Isolation Forest ML Anomaly Detection & Scoring
4. NetworkX Dynamic Runtime Topology Discovery & Dependency Mapping
5. Graph Pathway Scoring, Root-Cause Candidate Ranking & Blast Radius Isolation
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

import networkx as nx
import pytest
import pytest_asyncio

from drain3.file_persistence import FilePersistence

from backend.app.ml.anomaly_detector import IsolationForestAnomalyDetector
from backend.app.ml.feature_extractor import SlidingWindowFeatureExtractor, WindowConfig
from backend.app.models import FeatureVector, LogWindow, ParsedLog
from backend.app.schemas.blast_radius import BlastRadiusResult, ServiceAnomalyEvidence
from backend.app.services.drain_parser import DrainParser
from backend.app.services.graph_scorer import (
    DynamicGraphPathwayScorer,
    DynamicGraphScorerConfig,
)
from backend.app.services.runtime_dependency_parser import (
    RuntimeDependencyParser,
    TraceObservation,
)
from backend.app.services.topology_pipeline import NetworkXTopologyPipeline

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def drain_parser(tmp_path: Path) -> DrainParser:
    file_pers = FilePersistence(str(tmp_path / "drain3_state.bin"))
    return DrainParser(persistence=file_pers)


@pytest.fixture
def topology_pipeline() -> NetworkXTopologyPipeline:
    return NetworkXTopologyPipeline()


@pytest.fixture
def dependency_parser() -> RuntimeDependencyParser:
    return RuntimeDependencyParser()


@pytest.fixture
def window_config() -> WindowConfig:
    return WindowConfig(
        window_size_seconds=10,
        stride_seconds=5,
        min_logs_per_window=3,
    )


@pytest.fixture
def feature_extractor(window_config: WindowConfig) -> SlidingWindowFeatureExtractor:
    return SlidingWindowFeatureExtractor(config=window_config)


@pytest.fixture
def trained_detector() -> IsolationForestAnomalyDetector:
    """Train a deterministic in-memory Isolation Forest model on synthetic baseline FeatureVector objects."""
    detector = IsolationForestAnomalyDetector(
        contamination=0.05,
        random_state=42,
    )
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    baseline_vectors: List[FeatureVector] = []

    for i in range(40):
        log_cnt = random_int(20, 60)
        err_cnt = random_int(0, 1)
        warn_cnt = random_int(0, 2)
        info_cnt = log_cnt - err_cnt - warn_cnt
        err_ratio = err_cnt / log_cnt
        active_svcs = 4
        unique_tpls = random_int(3, 8)
        dom_svc_cnt = random_int(10, 25)
        dom_tpl_cnt = random_int(8, 20)
        logs_per_sec = log_cnt / 10.0
        avg_logs_per_min = logs_per_sec * 60.0
        burst_ind = 0.0

        vec = FeatureVector(
            window_id=f"win-train-{i}",
            timestamp=now + timedelta(seconds=i * 10),
            window_start=now + timedelta(seconds=i * 10),
            window_end=now + timedelta(seconds=(i + 1) * 10),
            log_count=log_cnt,
            unique_templates=unique_tpls,
            error_count=err_cnt,
            warning_count=warn_cnt,
            features={
                "log_count": float(log_cnt),
                "info_count": float(info_cnt),
                "warning_count": float(warn_cnt),
                "error_count": float(err_cnt),
                "error_ratio": float(err_ratio),
                "active_services": float(active_svcs),
                "unique_templates": float(unique_tpls),
                "dominant_service_count": float(dom_svc_cnt),
                "dominant_template_count": float(dom_tpl_cnt),
                "logs_per_second": float(logs_per_sec),
                "avg_logs_per_minute": float(avg_logs_per_min),
                "burst_indicator": float(burst_ind),
            },
        )
        baseline_vectors.append(vec)

    detector.train(baseline_vectors)
    return detector


def random_int(a: int, b: int) -> int:
    import random
    return random.randint(a, b)


# ---------------------------------------------------------------------------
# Pipeline Tests
# ---------------------------------------------------------------------------

async def test_e2e_drain3_template_mining_and_masking(drain_parser: DrainParser) -> None:
    """Test 1: Validate Drain3 parses raw logs, extracts templates and masks variables."""
    raw_logs = [
        "POST /api/v1/orders HTTP/1.1 200 from 192.168.1.42 user_id=usr_98124 duration=12ms",
        "POST /api/v1/orders HTTP/1.1 200 from 192.168.1.88 user_id=usr_55412 duration=18ms",
        "POST /api/v1/orders HTTP/1.1 504 from 10.0.4.15 user_id=usr_11928 duration=5002ms",
        "FATAL: postgres connection pool exhausted (active=100/100, queued=450)",
        "FATAL: postgres connection pool exhausted (active=100/100, queued=512)",
    ]

    parsed_results = []
    for raw in raw_logs:
        parsed = drain_parser.parse(
            raw,
            metadata={"service": "order-service", "level": "INFO" if "200" in raw else "ERROR"},
        )
        parsed_results.append(parsed)

    assert len(parsed_results) == 5
    templates = {p["template_text"] for p in parsed_results}
    assert len(templates) < len(raw_logs), "Drain3 should cluster similar log patterns"

    # Assert variable parameters were clustered under same template
    postgres_templates = [p for p in parsed_results if "postgres" in p["raw_message"]]
    assert len(postgres_templates) == 2
    assert postgres_templates[0]["template_id"] == postgres_templates[1]["template_id"]


async def test_e2e_sliding_window_feature_extraction(
    feature_extractor: SlidingWindowFeatureExtractor,
) -> None:
    """Test 2: Validate feature extraction aggregates statistical metrics across time windows."""
    base_time = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

    # Ingest 15 normal logs spanning 8 seconds
    for i in range(15):
        log_time = base_time + timedelta(seconds=i * 0.5)
        parsed = ParsedLog(
            id=f"01J5E2Z00000000000000000{i:02d}",
            service="order-service",
            raw_message=f"Order created order_id=ord_{i} amount=${10 + i}.00",
            template_id="t1",
            template_text="Order created order_id=<*:NUM> amount=$<*:NUM>",
            parameters=[{"name": "param_0", "value": str(i)}],
            timestamp=log_time,
            level="INFO",
        )
        feature_extractor.add_log(parsed)

    # Ingest 5 error logs
    for i in range(5):
        log_time = base_time + timedelta(seconds=4 + i * 0.5)
        parsed = ParsedLog(
            id=f"01J5E2Z00000000000000001{i:02d}",
            service="order-service",
            raw_message=f"Database query timeout after 5000ms query_id=q_{i}",
            template_id="t2",
            template_text="Database query timeout after <*:NUM>ms query_id=<*:NUM>",
            parameters=[{"name": "param_0", "value": str(i)}],
            timestamp=log_time,
            level="ERROR",
        )
        feature_extractor.add_log(parsed)

    # Trigger window generation
    cutoff_time = base_time + timedelta(seconds=12)
    pending_windows = feature_extractor.get_pending_windows(current_time=cutoff_time)

    assert len(pending_windows) >= 1, "At least one sliding window should have closed"
    window = pending_windows[0]
    feature_vector = feature_extractor.extract_features(window)

    assert feature_vector.log_count >= 10
    assert feature_vector.error_count == 5
    assert feature_vector.features["error_ratio"] > 0.0
    assert "active_services" in feature_vector.features
    assert "unique_templates" in feature_vector.features


async def test_e2e_isolation_forest_anomaly_detection(
    trained_detector: IsolationForestAnomalyDetector,
) -> None:
    """Test 3: Validate Isolation Forest identifies normal vs cascading anomaly vectors."""
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)

    # 1. Normal steady-state vector
    normal_vec = FeatureVector(
        window_id="win-norm-1",
        timestamp=now,
        window_start=now,
        window_end=now + timedelta(seconds=10),
        log_count=35,
        unique_templates=5,
        error_count=0,
        warning_count=1,
        features={
            "log_count": 35.0,
            "info_count": 34.0,
            "warning_count": 1.0,
            "error_count": 0.0,
            "error_ratio": 0.0,
            "active_services": 4.0,
            "unique_templates": 5.0,
            "dominant_service_count": 15.0,
            "dominant_template_count": 12.0,
            "logs_per_second": 3.5,
            "avg_logs_per_minute": 210.0,
            "burst_indicator": 0.0,
        },
    )
    normal_result = trained_detector.predict(normal_vec)
    assert normal_result["is_anomaly"] is False
    assert normal_result["anomaly_score"] < 0.65

    # 2. Incident vector (100% error rate, massive burst, high error count)
    incident_vec = FeatureVector(
        window_id="win-incident-1",
        timestamp=now,
        window_start=now,
        window_end=now + timedelta(seconds=10),
        log_count=200,
        unique_templates=12,
        error_count=180,
        warning_count=10,
        features={
            "log_count": 200.0,
            "info_count": 10.0,
            "warning_count": 10.0,
            "error_count": 180.0,
            "error_ratio": 0.90,
            "active_services": 5.0,
            "unique_templates": 12.0,
            "dominant_service_count": 80.0,
            "dominant_template_count": 75.0,
            "logs_per_second": 20.0,
            "avg_logs_per_minute": 1200.0,
            "burst_indicator": 1.0,
        },
    )
    incident_result = trained_detector.predict(incident_vec)
    assert incident_result["is_anomaly"] is True
    assert incident_result["severity"] in ("low", "medium", "high", "critical")


async def test_e2e_topology_dynamic_dependency_inference(
    topology_pipeline: NetworkXTopologyPipeline,
) -> None:
    """Test 4: Validate caller -> callee dependency discovery from trace spans."""
    now = datetime.now(timezone.utc)
    txn_id = "txn-ecommerce-1"

    # 1. API Gateway -> Order Service call
    obs1 = TraceObservation(
        canonical_transaction_id=txn_id,
        service="api-gateway",
        timestamp=now,
        template_id="tpl-gw",
        span_id="span-gw",
        parent_span_id=None,
        target_service_hint="order-service",
    )
    topology_pipeline.add_observation(obs1)

    # 2. Order Service -> Payment Gateway call
    obs2 = TraceObservation(
        canonical_transaction_id=txn_id,
        service="order-service",
        timestamp=now + timedelta(milliseconds=10),
        template_id="tpl-ord",
        span_id="span-ord",
        parent_span_id="span-gw",
        target_service_hint="payment-gateway",
    )
    topology_pipeline.add_observation(obs2)

    # 3. Payment Gateway -> Postgres DB call
    obs3 = TraceObservation(
        canonical_transaction_id=txn_id,
        service="payment-gateway",
        timestamp=now + timedelta(milliseconds=20),
        template_id="tpl-pay",
        span_id="span-pay",
        parent_span_id="span-ord",
        target_service_hint="postgres-db",
    )
    topology_pipeline.add_observation(obs3)

    # Also add final Postgres observation in the same trace
    obs4 = TraceObservation(
        canonical_transaction_id=txn_id,
        service="postgres-db",
        timestamp=now + timedelta(milliseconds=30),
        template_id="tpl-db",
        span_id="span-db",
        parent_span_id="span-pay",
    )
    topology_pipeline.add_observation(obs4)

    snapshot = topology_pipeline.get_snapshot()

    assert snapshot["node_count"] >= 4
    nodes = {n["id"] for n in snapshot["nodes"]}
    assert {"api-gateway", "order-service", "payment-gateway", "postgres-db"}.issubset(nodes)

    edge_pairs = {(e["source"], e["target"]) for e in snapshot["edges"]}
    assert ("api-gateway", "order-service") in edge_pairs
    assert ("order-service", "payment-gateway") in edge_pairs
    assert ("payment-gateway", "postgres-db") in edge_pairs


async def test_e2e_graph_pathway_root_cause_and_blast_radius(
    topology_pipeline: NetworkXTopologyPipeline,
) -> None:
    """Test 5: Validate DynamicGraphPathwayScorer isolates root cause and builds blast radius."""
    now = datetime.now(timezone.utc)
    trace_id = "trace-incident-test"
    
    # Build caller -> callee topology
    topology_pipeline.add_observation(
        TraceObservation(
            canonical_transaction_id=trace_id,
            service="api-gateway",
            timestamp=now,
            template_id="tpl-gw",
            span_id="span-gw",
            parent_span_id=None,
            target_service_hint="order-service",
        )
    )
    topology_pipeline.add_observation(
        TraceObservation(
            canonical_transaction_id=trace_id,
            service="order-service",
            timestamp=now + timedelta(milliseconds=5),
            template_id="tpl-ord",
            span_id="span-ord",
            parent_span_id="span-gw",
            target_service_hint="payment-gateway",
        )
    )
    topology_pipeline.add_observation(
        TraceObservation(
            canonical_transaction_id=trace_id,
            service="payment-gateway",
            timestamp=now + timedelta(milliseconds=10),
            template_id="tpl-pay",
            span_id="span-pay",
            parent_span_id="span-ord",
            target_service_hint="postgres-db",
        )
    )
    topology_pipeline.add_observation(
        TraceObservation(
            canonical_transaction_id=trace_id,
            service="postgres-db",
            timestamp=now + timedelta(milliseconds=15),
            template_id="tpl-db",
            span_id="span-db",
            parent_span_id="span-pay",
        )
    )

    scorer = DynamicGraphPathwayScorer(
        DynamicGraphScorerConfig(minimum_blast_radius_impact_threshold=0.0)
    )
    graph = topology_pipeline.build_graph()

    # Inject anomaly evidence where postgres-db failed earliest with highest anomaly score
    evidence_list = [
        ServiceAnomalyEvidence(
            service_name="postgres-db",
            anomaly_score=0.96,
            severity_score=1.0,
            observed_at=now - timedelta(seconds=20),
            correlation_ids=[trace_id],
            event_ids=["evt-db-1"],
        ),
        ServiceAnomalyEvidence(
            service_name="payment-gateway",
            anomaly_score=0.88,
            severity_score=0.85,
            observed_at=now - timedelta(seconds=15),
            correlation_ids=[trace_id],
            event_ids=["evt-pay-1"],
        ),
        ServiceAnomalyEvidence(
            service_name="order-service",
            anomaly_score=0.82,
            severity_score=0.80,
            observed_at=now - timedelta(seconds=10),
            correlation_ids=[trace_id],
            event_ids=["evt-ord-1"],
        ),
        ServiceAnomalyEvidence(
            service_name="api-gateway",
            anomaly_score=0.74,
            severity_score=0.70,
            observed_at=now - timedelta(seconds=5),
            correlation_ids=[trace_id],
            event_ids=["evt-gw-1"],
        ),
    ]

    result = scorer.score(
        graph=graph,
        evidence=evidence_list,
        calculated_at=now,
    )

    assert result is not None
    assert result.suspected_root_service == "postgres-db"
    assert result.confidence > 0.50
    assert len(result.blast_radius) >= 3

    # Check root vs cascade impact classifications
    root_nodes = [n for n in result.blast_radius if n.impact_classification == "root"]
    assert len(root_nodes) == 1
    assert root_nodes[0].service_name == "postgres-db"

    affected_services = {n.service_name for n in result.blast_radius}
    assert "payment-gateway" in affected_services
    assert "order-service" in affected_services

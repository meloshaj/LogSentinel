import pytest
import networkx as nx
from datetime import datetime, timezone, timedelta
from app.services.graph_scorer import DynamicGraphPathwayScorer, DynamicGraphScorerConfig
from app.schemas.blast_radius import ServiceAnomalyEvidence

@pytest.fixture
def base_topology() -> nx.DiGraph:
    """Create a standard multi-tier e-commerce topology."""
    G = nx.DiGraph()
    # Adding edges with transaction_count to support edge_strength scoring
    G.add_edge("api-gateway", "auth-service", transition_count=100)
    G.add_edge("api-gateway", "order-service", transition_count=80)
    G.add_edge("auth-service", "redis-cache", transition_count=120)
    G.add_edge("order-service", "payment-gateway", transition_count=30)
    G.add_edge("order-service", "postgres-db", transition_count=50)
    G.add_edge("payment-gateway", "postgres-db", transition_count=30)
    return G

@pytest.fixture
def scorer() -> DynamicGraphPathwayScorer:
    """Return a scorer with deterministic configuration."""
    config = DynamicGraphScorerConfig(
        temporal_decay_seconds=300.0,
        hop_decay=0.75,
        neutral_edge_strength=0.5
    )
    return DynamicGraphPathwayScorer(config=config)

def test_upstream_propagation_identifies_root_cause(
    base_topology: nx.DiGraph, scorer: DynamicGraphPathwayScorer
):
    """
    Scenario: Database fails, which bubbles up to order-service and then api-gateway.
    The database should be correctly identified as the root cause, and the blast
    radius should include all affected upstream services.
    """
    t0 = datetime.now(timezone.utc)
    
    # Evidence is collected from three services sharing a correlation trace
    trace_id = "trace-12345"
    evidence = [
        ServiceAnomalyEvidence(
            service_name="postgres-db",
            anomaly_score=0.95,
            severity_score=0.9,
            observed_at=t0,
            correlation_ids=[trace_id],
            event_ids=["evt-db-1"]
        ),
        ServiceAnomalyEvidence(
            service_name="order-service",
            anomaly_score=0.85,
            severity_score=0.8,
            observed_at=t0 + timedelta(seconds=1),
            correlation_ids=[trace_id],
            event_ids=["evt-order-1"]
        ),
        ServiceAnomalyEvidence(
            service_name="api-gateway",
            anomaly_score=0.75,
            severity_score=0.8,
            observed_at=t0 + timedelta(seconds=2),
            correlation_ids=[trace_id],
            event_ids=["evt-api-1"]
        ),
    ]

    result = scorer.score(base_topology, evidence, calculated_at=t0 + timedelta(seconds=3))
    
    # Assertions
    assert result.suspected_root_service == "postgres-db"
    assert "order-service" in result.affected_services
    assert "api-gateway" in result.affected_services
    
    # Check that root classification is correct in blast radius
    root_nodes = [n for n in result.blast_radius if n.impact_classification == "root"]
    assert len(root_nodes) == 1
    assert root_nodes[0].service_name == "postgres-db"

def test_independent_cascades_stay_separated(
    base_topology: nx.DiGraph, scorer: DynamicGraphPathwayScorer
):
    """
    Scenario: Two unrelated anomalies occur simultaneously in different parts of the graph
    (e.g., redis-cache and payment-gateway). The scorer should isolate the most
    significant one as the root and not incorrectly merge pathways.
    """
    t0 = datetime.now(timezone.utc)
    
    evidence = [
        # Cascade 1
        ServiceAnomalyEvidence(
            service_name="redis-cache",
            anomaly_score=0.99,
            severity_score=1.0,
            observed_at=t0,
            correlation_ids=["trace-auth"],
            event_ids=["evt-redis-1"]
        ),
        ServiceAnomalyEvidence(
            service_name="auth-service",
            anomaly_score=0.8,
            severity_score=0.7,
            observed_at=t0 + timedelta(seconds=1),
            correlation_ids=["trace-auth"],
            event_ids=["evt-auth-1"]
        ),
        # Cascade 2 (independent)
        ServiceAnomalyEvidence(
            service_name="payment-gateway",
            anomaly_score=0.90,
            severity_score=0.9,
            observed_at=t0,
            correlation_ids=["trace-pay"],
            event_ids=["evt-pay-1"]
        ),
    ]

    result = scorer.score(base_topology, evidence, calculated_at=t0 + timedelta(seconds=2))
    
    # The redis cascade is slightly stronger, so it should win as root
    assert result.suspected_root_service == "redis-cache"
    
    # Check pathways: payment-gateway should not be forcefully connected to redis
    assert "payment-gateway" not in result.affected_services

def test_missing_path_recovery(
    base_topology: nx.DiGraph, scorer: DynamicGraphPathwayScorer
):
    """
    Scenario: An upstream service shows anomalous, but the intermediate service is missing
    (or doesn't report telemetry). The scorer should still be able to connect the 
    start and end of the dependency path if a path exists in the topology graph.
    """
    t0 = datetime.now(timezone.utc)
    
    evidence = [
        ServiceAnomalyEvidence(
            service_name="postgres-db",
            anomaly_score=0.95,
            severity_score=0.9,
            observed_at=t0,
            correlation_ids=["trace-skip"],
            event_ids=["evt-db-2"]
        ),
        # Notice we omit order-service evidence
        ServiceAnomalyEvidence(
            service_name="api-gateway",
            anomaly_score=0.80,
            severity_score=0.8,
            observed_at=t0 + timedelta(seconds=2),
            correlation_ids=["trace-skip"],
            event_ids=["evt-api-2"]
        ),
    ]

    result = scorer.score(base_topology, evidence, calculated_at=t0 + timedelta(seconds=3))
    
    # Should still trace api-gateway back to postgres-db
    assert result.suspected_root_service == "postgres-db"
    assert "api-gateway" in result.affected_services
    
    # Verify the path contains order-service even if it wasn't anomalous
    gateway_node = next((n for n in result.blast_radius if n.service_name == "api-gateway"), None)
    assert gateway_node is not None
    assert "order-service" in gateway_node.dependency_path

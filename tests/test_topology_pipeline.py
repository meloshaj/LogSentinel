from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.app.models import ParsedLog
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.services.runtime_dependency_parser import (
    RuntimeDependencyParser,
    TraceObservation,
)
from backend.app.services.topology_pipeline import NetworkXTopologyPipeline
from backend.app.workers.drain_worker import DrainWorker


BASE_TIME = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)


def observation(
    transaction_id: str | None = "txn-1",
    service: str = "gateway",
    *,
    offset_ms: float = 0,
    environment: str | None = "test",
    span_id: str | None = None,
    parent_span_id: str | None = None,
    template_id: str = "template-1",
) -> TraceObservation:
    return TraceObservation(
        canonical_transaction_id=transaction_id,
        service=service,
        timestamp=BASE_TIME + timedelta(milliseconds=offset_ms),
        template_id=template_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        environment=environment,
        source="unit-test",
    )


def edge_attrs(graph, source: str, target: str) -> dict[str, Any]:
    return graph.edges[source, target]


class StubLogBuffer:
    async def dequeue(self):
        raise RuntimeError("not used")

    async def join(self) -> None:
        return None

    def task_done(self) -> None:
        return None

    def queue_size(self) -> int:
        return 0


class RecordingBatchManager(ParsedLogBatchManager):
    def __init__(self) -> None:
        super().__init__()
        self.received: list[ParsedLog] = []

    async def add(self, parsed_log: ParsedLog) -> None:
        self.received.append(parsed_log)


class MetadataPreservingParser:
    def parse(self, raw_message: str, metadata: dict | None = None) -> ParsedLog:
        metadata = metadata or {}
        timestamp = metadata.get("timestamp") or BASE_TIME
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return ParsedLog(
            timestamp=timestamp,
            service=str(metadata.get("service", "test-service")),
            level=str(metadata.get("level", "info")),
            raw_message=raw_message,
            template_id=f"template-{raw_message}",
            template_text=raw_message,
            parameters=[],
            source=metadata.get("source"),
            environment=metadata.get("environment"),
            correlation_id=metadata.get("correlation_id"),
            metadata=dict(metadata),
            parsed_at=BASE_TIME,
        )


def test_single_observation_temporal_vector_offset_is_zero() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))

    vector = pipeline.get_transaction_vector("txn-1", environment="test")

    assert vector[0]["service"] == "gateway"
    assert vector[0]["start_offset_ms"] == 0


def test_multiple_observations_are_ordered_chronologically() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="user", offset_ms=85))
    pipeline.add_observation(observation(service="gateway", offset_ms=0))
    pipeline.add_observation(observation(service="auth", offset_ms=30))

    vector = pipeline.get_transaction_vector("txn-1", environment="test")

    assert [entry["service"] for entry in vector] == ["gateway", "auth", "user"]


def test_start_offsets_are_calculated_in_milliseconds() -> None:
    pipeline = NetworkXTopologyPipeline()
    for service, offset in [("gateway", 0), ("auth", 30), ("user", 85.5)]:
        pipeline.add_observation(observation(service=service, offset_ms=offset))

    vector = pipeline.get_transaction_vector("txn-1", environment="test")

    assert [entry["start_offset_ms"] for entry in vector] == [0, 30, 85.5]


def test_equal_timestamps_use_insertion_order_tie_breaker() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="auth"))
    pipeline.add_observation(observation(service="gateway"))

    vector = pipeline.get_transaction_vector("txn-1", environment="test")

    assert [entry["service"] for entry in vector] == ["auth", "gateway"]
    assert [entry["insertion_order"] for entry in vector] == [0, 1]


def test_input_observations_are_not_mutated() -> None:
    pipeline = NetworkXTopologyPipeline()
    original = observation(service="gateway", span_id="span-1")
    before = deepcopy(original.model_dump(mode="json"))

    pipeline.add_observation(original)
    pipeline.get_snapshot()

    assert original.model_dump(mode="json") == before


def test_repeated_vector_retrieval_is_idempotent() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))
    pipeline.add_observation(observation(service="auth", offset_ms=10))

    first = pipeline.get_transaction_vector("txn-1", environment="test")
    second = pipeline.get_transaction_vector("txn-1", environment="test")

    assert first == second


def test_graph_has_one_node_per_distinct_service() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))
    pipeline.add_observation(observation(service="gateway", offset_ms=10))
    pipeline.add_observation(observation(service="auth", offset_ms=20))

    graph = pipeline.build_graph()

    assert sorted(graph.nodes) == ["auth", "gateway"]


def test_node_event_count_counts_all_service_observations() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))
    pipeline.add_observation(observation(service="gateway", offset_ms=10))

    graph = pipeline.build_graph()

    assert graph.nodes["gateway"]["event_count"] == 2


def test_node_transaction_count_counts_unique_transactions() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation("txn-1", service="gateway"))
    pipeline.add_observation(observation("txn-1", service="gateway", offset_ms=10))
    pipeline.add_observation(observation("txn-2", service="gateway", offset_ms=20))

    graph = pipeline.build_graph()

    assert graph.nodes["gateway"]["transaction_count"] == 2


def test_node_first_seen_and_last_seen_are_correct() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway", offset_ms=50))
    pipeline.add_observation(observation(service="gateway", offset_ms=10))

    graph = pipeline.build_graph()

    assert graph.nodes["gateway"]["first_seen"] == BASE_TIME + timedelta(milliseconds=10)
    assert graph.nodes["gateway"]["last_seen"] == BASE_TIME + timedelta(milliseconds=50)


def test_node_start_offset_minimum_maximum_and_average_are_correct() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway", offset_ms=0))
    pipeline.add_observation(observation(service="gateway", offset_ms=20))
    pipeline.add_observation(observation(service="gateway", offset_ms=40))

    graph = pipeline.build_graph()
    node = graph.nodes["gateway"]

    assert node["minimum_start_offset_ms"] == 0
    assert node["maximum_start_offset_ms"] == 40
    assert node["average_start_offset_ms"] == 20


def test_temporal_gateway_auth_user_produces_two_directed_edges() -> None:
    pipeline = NetworkXTopologyPipeline()
    for service, offset in [("gateway", 0), ("auth", 30), ("user", 85)]:
        pipeline.add_observation(observation(service=service, offset_ms=offset))

    graph = pipeline.build_graph()

    assert sorted(graph.edges) == [("auth", "user"), ("gateway", "auth")]


def test_consecutive_duplicate_services_do_not_create_self_loops() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))
    pipeline.add_observation(observation(service="gateway", offset_ms=10))

    graph = pipeline.build_graph()

    assert graph.number_of_edges() == 0


def test_different_transactions_do_not_become_connected() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation("txn-1", service="gateway"))
    pipeline.add_observation(observation("txn-2", service="auth", offset_ms=10))

    graph = pipeline.build_graph()

    assert graph.number_of_edges() == 0


def test_temporal_edge_delays_are_calculated_correctly() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway", offset_ms=0))
    pipeline.add_observation(observation(service="auth", offset_ms=42.25))

    graph = pipeline.build_graph()

    assert edge_attrs(graph, "gateway", "auth")["minimum_delay_ms"] == 42.25
    assert edge_attrs(graph, "gateway", "auth")["maximum_delay_ms"] == 42.25
    assert edge_attrs(graph, "gateway", "auth")["average_delay_ms"] == 42.25


def test_parent_span_maps_to_child_parent_span_id() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway", span_id="span-gateway"))
    pipeline.add_observation(
        observation(service="auth", offset_ms=10, parent_span_id="span-gateway")
    )

    graph = pipeline.build_graph()

    assert ("gateway", "auth") in graph.edges
    assert edge_attrs(graph, "gateway", "auth")["span_evidence_count"] == 1


def test_span_parent_evidence_is_preferred_over_temporal_duplicate_counting() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway", span_id="span-gateway"))
    pipeline.add_observation(
        observation(service="auth", offset_ms=10, parent_span_id="span-gateway")
    )

    graph = pipeline.build_graph()
    edge = edge_attrs(graph, "gateway", "auth")

    assert edge["transition_count"] == 1
    assert edge["transaction_count"] == 1
    assert edge["span_evidence_count"] == 1
    assert edge["temporal_evidence_count"] == 1


def test_cross_transaction_span_ids_do_not_create_edges() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation("txn-1", service="gateway", span_id="shared"))
    pipeline.add_observation(
        observation("txn-2", service="auth", offset_ms=10, parent_span_id="shared")
    )

    graph = pipeline.build_graph()

    assert graph.number_of_edges() == 0


def test_ambiguous_span_relationship_falls_back_to_temporal_edges() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway", span_id="ambiguous"))
    pipeline.add_observation(observation(service="worker", offset_ms=5, span_id="ambiguous"))
    pipeline.add_observation(
        observation(service="auth", offset_ms=10, parent_span_id="ambiguous")
    )

    graph = pipeline.build_graph()

    assert edge_attrs(graph, "gateway", "worker")["temporal_evidence_count"] == 1
    assert edge_attrs(graph, "worker", "auth")["temporal_evidence_count"] == 1
    assert all(attrs["span_evidence_count"] == 0 for _, _, attrs in graph.edges(data=True))


def test_negative_parent_child_delay_is_not_reported_as_valid_latency() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(
        observation(service="auth", offset_ms=0, parent_span_id="span-gateway")
    )
    pipeline.add_observation(
        observation(service="gateway", offset_ms=20, span_id="span-gateway")
    )

    graph = pipeline.build_graph()
    edge = edge_attrs(graph, "gateway", "auth")

    assert edge["span_evidence_count"] == 1
    assert edge["minimum_delay_ms"] is None
    assert edge["maximum_delay_ms"] is None
    assert edge["average_delay_ms"] is None


def test_repeated_transactions_increment_aggregate_edge_counts() -> None:
    pipeline = NetworkXTopologyPipeline()
    for transaction_id in ["txn-1", "txn-2"]:
        pipeline.add_observation(observation(transaction_id, service="gateway"))
        pipeline.add_observation(observation(transaction_id, service="auth", offset_ms=10))

    graph = pipeline.build_graph()
    edge = edge_attrs(graph, "gateway", "auth")

    assert edge["transition_count"] == 2
    assert edge["transaction_count"] == 2


def test_edge_transaction_count_remains_unique_per_transaction() -> None:
    pipeline = NetworkXTopologyPipeline()
    for offset in [0, 10, 20, 30]:
        service = "gateway" if offset in [0, 20] else "auth"
        pipeline.add_observation(observation("txn-1", service=service, offset_ms=offset))

    graph = pipeline.build_graph()
    edge = edge_attrs(graph, "gateway", "auth")

    assert edge["transition_count"] == 1
    assert edge["transaction_count"] == 1


def test_build_graph_twice_does_not_double_count() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))
    pipeline.add_observation(observation(service="auth", offset_ms=10))

    first = pipeline.build_graph()
    second = pipeline.build_graph()

    assert edge_attrs(first, "gateway", "auth")["transition_count"] == 1
    assert edge_attrs(second, "gateway", "auth")["transition_count"] == 1


def test_get_snapshot_twice_does_not_double_count() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))
    pipeline.add_observation(observation(service="auth", offset_ms=10))

    first = pipeline.get_snapshot()
    second = pipeline.get_snapshot()

    assert first == second


def test_returned_graph_mutation_does_not_mutate_pipeline_state() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))

    graph = pipeline.build_graph()
    graph.add_node("mutated")

    assert "mutated" not in pipeline.build_graph().nodes


def test_max_transactions_evicts_deterministically() -> None:
    pipeline = NetworkXTopologyPipeline(max_transactions=1)
    pipeline.add_observation(observation("txn-1", service="gateway"))
    pipeline.add_observation(observation("txn-2", service="auth", offset_ms=10))

    assert pipeline.get_transaction_vector("txn-1", environment="test") == []
    assert pipeline.get_transaction_vector("txn-2", environment="test")[0]["service"] == "auth"


def test_max_observations_per_transaction_is_enforced() -> None:
    pipeline = NetworkXTopologyPipeline(max_observations_per_transaction=2)
    pipeline.add_observation(observation(service="gateway", offset_ms=0))
    pipeline.add_observation(observation(service="auth", offset_ms=10))
    pipeline.add_observation(observation(service="user", offset_ms=20))

    vector = pipeline.get_transaction_vector("txn-1", environment="test")

    assert [entry["service"] for entry in vector] == ["auth", "user"]


def test_eviction_statistics_are_accurate() -> None:
    pipeline = NetworkXTopologyPipeline(
        max_transactions=1,
        max_observations_per_transaction=1,
    )
    pipeline.add_observation(observation("txn-1", service="gateway"))
    pipeline.add_observation(observation("txn-1", service="auth", offset_ms=10))
    pipeline.add_observation(observation("txn-2", service="user", offset_ms=20))

    stats = pipeline.get_stats()

    assert stats["evicted_transaction_count"] == 1
    assert stats["evicted_observation_count"] == 2
    assert stats["stored_observation_count"] == 1


def test_invalid_limits_are_rejected() -> None:
    with pytest.raises(ValueError):
        NetworkXTopologyPipeline(max_transactions=0)
    with pytest.raises(ValueError):
        NetworkXTopologyPipeline(max_observations_per_transaction=0)


def test_observation_without_canonical_transaction_id_is_rejected() -> None:
    pipeline = NetworkXTopologyPipeline()

    accepted = pipeline.add_observation(observation(None, service="gateway"))

    assert accepted is False
    assert pipeline.get_stats()["rejected_observation_count"] == 1


def test_observation_without_usable_service_is_rejected() -> None:
    pipeline = NetworkXTopologyPipeline()

    accepted = pipeline.add_observation(observation("txn-1", service="   "))

    assert accepted is False
    assert pipeline.get_stats()["rejected_observation_count"] == 1


def test_identical_transaction_ids_in_different_environments_remain_separate() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation("txn-shared", service="dev-gateway", environment="dev"))
    pipeline.add_observation(
        observation("txn-shared", service="prod-gateway", environment="prod")
    )

    assert pipeline.get_transaction_vector("txn-shared", environment="dev")[0]["service"] == "dev-gateway"
    assert pipeline.get_transaction_vector("txn-shared", environment="prod")[0]["service"] == "prod-gateway"
    assert pipeline.get_transaction_vector("txn-shared") == []


def test_snapshot_is_json_compatible() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))

    json.dumps(pipeline.get_snapshot())


def test_snapshot_nodes_and_edges_are_sorted_deterministically() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="user", offset_ms=20))
    pipeline.add_observation(observation(service="gateway", offset_ms=0))
    pipeline.add_observation(observation(service="auth", offset_ms=10))

    snapshot = pipeline.get_snapshot()

    assert [node["id"] for node in snapshot["nodes"]] == ["auth", "gateway", "user"]
    assert [edge["id"] for edge in snapshot["edges"]] == ["auth->user", "gateway->auth"]


def test_snapshot_datetimes_are_serialized_consistently() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))

    snapshot = pipeline.get_snapshot()

    assert snapshot["generated_at"] == "2026-07-22T10:00:00Z"
    assert snapshot["nodes"][0]["first_seen"] == "2026-07-22T10:00:00Z"


def test_snapshot_does_not_expose_raw_messages_or_secrets() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(
        TraceObservation(
            canonical_transaction_id="txn-secret",
            service="gateway",
            timestamp=BASE_TIME,
            template_id="template-secret",
            environment="test",
            source="token=secret-value",
        )
    )

    encoded = json.dumps(pipeline.get_snapshot())

    assert "raw_message" not in encoded
    assert "secret-value" not in encoded


def test_snapshot_counts_match_graph_counts() -> None:
    pipeline = NetworkXTopologyPipeline()
    pipeline.add_observation(observation(service="gateway"))
    pipeline.add_observation(observation(service="auth", offset_ms=10))

    graph = pipeline.build_graph()
    snapshot = pipeline.get_snapshot()

    assert snapshot["node_count"] == graph.number_of_nodes()
    assert snapshot["edge_count"] == graph.number_of_edges()


def test_drain_worker_callback_sends_trace_observation_to_topology_pipeline() -> None:
    batch_manager = RecordingBatchManager()
    topology = NetworkXTopologyPipeline()
    worker = DrainWorker(
        StubLogBuffer(),
        MetadataPreservingParser(),  # type: ignore[arg-type]
        batch_manager=batch_manager,
        runtime_dependency_parser=RuntimeDependencyParser(),
        on_trace_observation=topology.add_observation,
    )

    asyncio.run(
        worker.process_one(
            {
                "environment": "test",
                "logs": [
                    {
                        "service_name": "gateway",
                        "message": "trace log",
                        "metadata": {"trace_id": "worker-topology-trace"},
                    }
                ],
            }
        )
    )

    assert topology.get_stats()["stored_observation_count"] == 1
    assert topology.get_transaction_vector("worker-topology-trace", environment="test")[0][
        "service"
    ] == "gateway"


def test_no_trace_log_does_not_create_topology_state() -> None:
    batch_manager = RecordingBatchManager()
    topology = NetworkXTopologyPipeline()
    worker = DrainWorker(
        StubLogBuffer(),
        MetadataPreservingParser(),  # type: ignore[arg-type]
        batch_manager=batch_manager,
        runtime_dependency_parser=RuntimeDependencyParser(),
        on_trace_observation=topology.add_observation,
    )

    asyncio.run(
        worker.process_one({"logs": [{"service_name": "gateway", "message": "no trace"}]})
    )

    assert len(batch_manager.received) == 1
    assert topology.get_stats()["stored_observation_count"] == 0


def test_topology_callback_exception_does_not_block_parsed_log_batching() -> None:
    batch_manager = RecordingBatchManager()

    def failing_callback(_: TraceObservation) -> bool:
        raise RuntimeError("topology unavailable")

    worker = DrainWorker(
        StubLogBuffer(),
        MetadataPreservingParser(),  # type: ignore[arg-type]
        batch_manager=batch_manager,
        runtime_dependency_parser=RuntimeDependencyParser(),
        on_trace_observation=failing_callback,
    )

    parsed_logs = asyncio.run(
        worker.process_one(
            {
                "logs": [
                    {
                        "service_name": "gateway",
                        "message": "trace log",
                        "metadata": {"trace_id": "callback-failure-trace"},
                    }
                ],
            }
        )
    )

    assert len(parsed_logs) == 1
    assert batch_manager.received == parsed_logs
    assert worker.get_stats()["processed_count"] == 1

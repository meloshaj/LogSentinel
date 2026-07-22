from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from backend.app.models import ParsedLog
from backend.app.services.runtime_dependency_parser import RuntimeDependencyParser


FIXED_TIMESTAMP = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
VALID_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
VALID_PARENT_ID = "00f067aa0ba902b7"
VALID_TRACEPARENT = f"00-{VALID_TRACE_ID}-{VALID_PARENT_ID}-01"


def make_log(
    *,
    correlation_id: str | None = None,
    parameters: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ParsedLog:
    return ParsedLog(
        timestamp=FIXED_TIMESTAMP,
        service="orders",
        level="info",
        raw_message="order processed",
        template_id="template-1",
        template_text="order processed",
        parameters=parameters or [],
        source="ingest",
        environment="test",
        correlation_id=correlation_id,
        metadata=metadata or {},
        parsed_at=FIXED_TIMESTAMP,
    )


def source_paths(observation) -> set[str]:
    return {source.source_path for source in observation.extraction_sources}


def test_extracts_direct_parsed_log_correlation_id() -> None:
    observation = RuntimeDependencyParser().extract(make_log(correlation_id="corr-123"))

    assert observation is not None
    assert observation.correlation_id == "corr-123"
    assert observation.canonical_transaction_id == "corr-123"
    assert "top_level.correlation_id" in source_paths(observation)


def test_extracts_nested_parameter_trace_id() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(parameters=[{"payload": {"trace_id": "trace-abc"}}])
    )

    assert observation is not None
    assert observation.trace_id == "trace-abc"
    assert observation.canonical_transaction_id == "trace-abc"
    assert "parameters[0].payload.trace_id" in source_paths(observation)


def test_extracts_nested_metadata_header_correlation_id() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(metadata={"headers": {"x-correlation-id": "corr-header"}})
    )

    assert observation is not None
    assert observation.correlation_id == "corr-header"
    assert "metadata.headers.x-correlation-id" in source_paths(observation)


def test_recognizes_camel_snake_dotted_and_hyphenated_key_variants() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(
            parameters=[
                {
                    "traceId": "trace-camel",
                    "span.id": "span-dotted",
                    "parent-span-id": "parent-hyphen",
                    "transaction_id": "txn-snake",
                    "request-id": "request-hyphen",
                }
            ]
        )
    )

    assert observation is not None
    assert observation.trace_id == "trace-camel"
    assert observation.span_id == "span-dotted"
    assert observation.parent_span_id == "parent-hyphen"
    assert observation.transaction_id == "txn-snake"
    assert observation.request_id == "request-hyphen"


def test_transaction_id_and_request_id_fallback_precedence() -> None:
    transaction_observation = RuntimeDependencyParser().extract(
        make_log(parameters=[{"request_id": "request-1", "transaction_id": "txn-1"}])
    )
    request_observation = RuntimeDependencyParser().extract(
        make_log(parameters=[{"request_id": "request-2"}])
    )

    assert transaction_observation is not None
    assert transaction_observation.canonical_transaction_id == "txn-1"
    assert request_observation is not None
    assert request_observation.canonical_transaction_id == "request-2"


def test_trace_id_wins_canonical_precedence() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(
            correlation_id="corr-1",
            parameters=[{"trace_id": "trace-1", "transaction_id": "txn-1"}],
            metadata={"request_id": "request-1"},
        )
    )

    assert observation is not None
    assert observation.canonical_transaction_id == "trace-1"


def test_span_id_is_not_used_as_canonical_transaction_id() -> None:
    observation = RuntimeDependencyParser().extract(make_log(parameters=[{"span_id": "span-only"}]))

    assert observation is not None
    assert observation.span_id == "span-only"
    assert observation.canonical_transaction_id is None


def test_valid_w3c_traceparent_extraction() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(metadata={"headers": {"traceparent": VALID_TRACEPARENT}})
    )

    assert observation is not None
    assert observation.trace_id == VALID_TRACE_ID
    assert observation.parent_span_id == VALID_PARENT_ID
    assert observation.canonical_transaction_id == VALID_TRACE_ID
    assert "metadata.headers.traceparent.trace_id" in source_paths(observation)
    assert "metadata.headers.traceparent.parent_span_id" in source_paths(observation)


def test_malformed_traceparent_is_rejected_without_throwing() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(metadata={"traceparent": "00-short-parent-01"})
    )

    assert observation is None


def test_all_zero_traceparent_is_rejected() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(metadata={"traceparent": f"00-{'0' * 32}-{'0' * 16}-01"})
    )

    assert observation is None


def test_json_string_extraction() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(parameters=[{"payload": '{"traceId": "json-trace", "spanId": "json-span"}'}])
    )

    assert observation is not None
    assert observation.trace_id == "json-trace"
    assert observation.span_id == "json-span"
    assert "parameters[0].payload.traceId" in source_paths(observation)


def test_conservative_key_value_extraction() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(metadata={"line": "trace_id=trace-kv span_id=span-kv correlation-id: corr-kv"})
    )

    assert observation is not None
    assert observation.trace_id == "trace-kv"
    assert observation.span_id == "span-kv"
    assert observation.correlation_id == "corr-kv"


def test_conflicting_identifier_precedence_is_recorded() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(correlation_id="top-corr", parameters=[{"correlation_id": "param-corr"}])
    )

    assert observation is not None
    assert observation.correlation_id == "top-corr"
    assert observation.conflicts == {"correlation_id": 1}


def test_source_path_recording_for_selected_identifier() -> None:
    observation = RuntimeDependencyParser().extract(make_log(parameters=[{"trace_id": "trace-src"}]))

    assert observation is not None
    assert observation.extraction_sources[0].identifier == "trace_id"
    assert observation.extraction_sources[0].source_path == "parameters[0].trace_id"


def test_deeply_nested_input_stops_at_configured_bound() -> None:
    parser = RuntimeDependencyParser(max_depth=2)

    observation = parser.extract(
        make_log(parameters=[{"a": {"b": {"trace_id": "too-deep"}}}])
    )

    assert observation is None


def test_cyclic_input_does_not_recurse_forever() -> None:
    log = make_log()
    cyclic: dict[str, Any] = {"trace_id": "cyclic-trace"}
    cyclic["self"] = cyclic
    log.parameters.append(cyclic)

    observation = RuntimeDependencyParser().extract(log)

    assert observation is not None
    assert observation.trace_id == "cyclic-trace"


def test_oversized_string_is_ignored() -> None:
    parser = RuntimeDependencyParser(max_string_length=32)

    observation = parser.extract(make_log(metadata={"line": "trace_id=too-large " + ("x" * 64)}))

    assert observation is None


def test_input_parsed_log_is_not_mutated() -> None:
    log = make_log(
        parameters=[{"payload": {"trace_id": "trace-immutable"}}],
        metadata={"headers": {"request_id": "request-immutable"}},
    )
    original_parameters = deepcopy(log.parameters)
    original_metadata = deepcopy(log.metadata)

    RuntimeDependencyParser().extract(log)

    assert log.parameters == original_parameters
    assert log.metadata == original_metadata


def test_no_trace_log_returns_none() -> None:
    observation = RuntimeDependencyParser().extract(
        make_log(parameters=[{"user_id": "not-a-trace-id"}], metadata={"status": "ok"})
    )

    assert observation is None

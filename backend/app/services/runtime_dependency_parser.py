"""Runtime extraction of distributed tracing context from parsed logs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models import ParsedLog

TraceIdentifierName = Literal[
    "transaction_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "correlation_id",
    "request_id",
]

ExtractionMethod = Literal[
    "top_level",
    "structured",
    "json_string",
    "key_value_text",
    "traceparent",
]


class TraceIdentifierSource(BaseModel):
    """Location and method used to select one normalized trace identifier."""

    identifier: TraceIdentifierName
    source_path: str
    method: ExtractionMethod


class TraceObservation(BaseModel):
    """Normalized tracing context derived from one ParsedLog."""

    canonical_transaction_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    correlation_id: str | None = None
    transaction_id: str | None = None
    request_id: str | None = None
    service: str
    timestamp: datetime
    template_id: str
    extraction_sources: list[TraceIdentifierSource] = Field(default_factory=list)
    conflicts: dict[TraceIdentifierName, int] = Field(default_factory=dict)
    environment: str | None = None
    source: str | None = None
    target_service_hint: str | None = None
    operation_hint: str | None = None


class _IdentifierOccurrence(BaseModel):
    identifier: TraceIdentifierName
    value: str
    source_path: str
    method: ExtractionMethod
    precedence: int
    order: int


class _TargetHintOccurrence(BaseModel):
    value: str
    precedence: int
    order: int


class RuntimeDependencyParser:
    """Extract normalized trace identifiers from structured ParsedLog fields.

    The parser is intentionally conservative: it only recognizes explicit
    tracing key names, bounds nested traversal, bounds string inspection, and
    never mutates the ParsedLog it receives.
    """

    DEFAULT_MAX_DEPTH = 8
    DEFAULT_MAX_INSPECTED_VALUES = 500
    DEFAULT_MAX_STRING_LENGTH = 4096
    DEFAULT_MAX_IDENTIFIER_LENGTH = 512

    _IDENTIFIER_ALIASES: dict[TraceIdentifierName, tuple[str, ...]] = {
        "trace_id": (
            "trace_id",
            "traceId",
            "trace-id",
            "trace.id",
            "x_trace_id",
            "x-trace-id",
            "otel_trace_id",
        ),
        "span_id": (
            "span_id",
            "spanId",
            "span-id",
            "span.id",
        ),
        "parent_span_id": (
            "parent_span_id",
            "parentSpanId",
            "parent-span-id",
            "parent.span.id",
        ),
        "correlation_id": (
            "correlation_id",
            "correlationId",
            "correlation-id",
            "x_correlation_id",
            "x-correlation-id",
        ),
        "transaction_id": (
            "transaction_id",
            "transactionId",
            "transaction-id",
            "txn_id",
            "tx_id",
        ),
        "request_id": (
            "request_id",
            "requestId",
            "request-id",
            "x_request_id",
            "x-request-id",
        ),
    }
    _TARGET_SERVICE_ALIASES = (
        "target_service",
        "destination_service",
        "downstream_service",
        "peer_service",
        "rpc_service",
        "http_host",
        "server_address",
        "failed_service",
        "dependency",
        "target",
    )
    _OPERATION_HINT_ALIASES = (
        "operation",
        "operation_name",
        "rpc_method",
        "http_route",
        "http_target",
    )

    _KEY_VALUE_PATTERN = re.compile(
        r"(?P<key>[A-Za-z][A-Za-z0-9_.-]{1,48})"
        r"\s*(?:=|:)\s*"
        r"(?P<value>\"[^\"\s]{1,256}\"|'[^'\s]{1,256}'|[A-Za-z0-9_.:/@+\-]{1,256})"
    )
    _TRACEPARENT_PATTERN = re.compile(
        r"^(?P<version>[0-9a-fA-F]{2})-"
        r"(?P<trace_id>[0-9a-fA-F]{32})-"
        r"(?P<parent_id>[0-9a-fA-F]{16})-"
        r"(?P<flags>[0-9a-fA-F]{2})$"
    )

    def __init__(
        self,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_inspected_values: int = DEFAULT_MAX_INSPECTED_VALUES,
        max_string_length: int = DEFAULT_MAX_STRING_LENGTH,
    ) -> None:
        if max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        if max_inspected_values <= 0:
            raise ValueError("max_inspected_values must be greater than 0")
        if max_string_length <= 0:
            raise ValueError("max_string_length must be greater than 0")

        self.max_depth = max_depth
        self.max_inspected_values = max_inspected_values
        self.max_string_length = max_string_length
        self._identifier_keys = self._build_identifier_key_map()
        self._target_service_keys = {
            self._normalize_key(alias) for alias in self._TARGET_SERVICE_ALIASES
        }
        self._operation_hint_keys = {
            self._normalize_key(alias) for alias in self._OPERATION_HINT_ALIASES
        }

    def extract(self, parsed_log: ParsedLog) -> TraceObservation | None:
        """Return a TraceObservation when the parsed log contains trace context."""
        occurrences: dict[TraceIdentifierName, list[_IdentifierOccurrence]] = {
            name: [] for name in self._IDENTIFIER_ALIASES
        }
        target_hints: list[_TargetHintOccurrence] = []
        operation_hints: list[_TargetHintOccurrence] = []
        state = {
            "order": 0,
            "inspected": 0,
            "seen": set(),
        }

        self._add_identifier(
            occurrences,
            "correlation_id",
            parsed_log.correlation_id,
            source_path="top_level.correlation_id",
            method="top_level",
            precedence=0,
            state=state,
        )
        self._walk_value(
            parsed_log.parameters,
            path="parameters",
            root="parameters",
            depth=0,
            method="structured",
            occurrences=occurrences,
            target_hints=target_hints,
            operation_hints=operation_hints,
            state=state,
        )
        self._walk_value(
            parsed_log.metadata,
            path="metadata",
            root="metadata",
            depth=0,
            method="structured",
            occurrences=occurrences,
            target_hints=target_hints,
            operation_hints=operation_hints,
            state=state,
        )

        selected = {
            identifier: self._select_identifier(identifier_occurrences)
            for identifier, identifier_occurrences in occurrences.items()
        }
        if not any(selected.values()):
            return None

        conflicts = self._conflict_counts(occurrences, selected)
        extraction_sources = [
            TraceIdentifierSource(
                identifier=occurrence.identifier,
                source_path=occurrence.source_path,
                method=occurrence.method,
            )
            for occurrence in selected.values()
            if occurrence is not None
        ]

        values = {
            identifier: occurrence.value if occurrence is not None else None
            for identifier, occurrence in selected.items()
        }
        canonical_transaction_id = self._canonical_transaction_id(values)

        return TraceObservation(
            canonical_transaction_id=canonical_transaction_id,
            trace_id=values["trace_id"],
            span_id=values["span_id"],
            parent_span_id=values["parent_span_id"],
            correlation_id=values["correlation_id"],
            transaction_id=values["transaction_id"],
            request_id=values["request_id"],
            service=parsed_log.service,
            timestamp=parsed_log.timestamp,
            template_id=parsed_log.template_id,
            extraction_sources=extraction_sources,
            conflicts=conflicts,
            environment=parsed_log.environment,
            source=parsed_log.source,
            target_service_hint=self._select_hint(target_hints),
            operation_hint=self._select_hint(operation_hints),
        )

    def _walk_value(
        self,
        value: Any,
        *,
        path: str,
        root: Literal["parameters", "metadata"],
        depth: int,
        method: Literal["structured", "json_string", "key_value_text"],
        occurrences: dict[TraceIdentifierName, list[_IdentifierOccurrence]],
        target_hints: list[_TargetHintOccurrence],
        operation_hints: list[_TargetHintOccurrence],
        state: dict[str, Any],
    ) -> None:
        if state["inspected"] >= self.max_inspected_values or depth > self.max_depth:
            return

        state["inspected"] += 1

        if isinstance(value, bytes | bytearray | memoryview):
            return

        if isinstance(value, Mapping):
            object_id = id(value)
            if object_id in state["seen"]:
                return
            state["seen"].add(object_id)

            for key, child_value in value.items():
                if state["inspected"] >= self.max_inspected_values:
                    break
                if not isinstance(key, str):
                    continue

                child_path = self._join_path(path, key)
                normalized_key = self._normalize_key(key)
                precedence = self._precedence(root, depth, method)

                if normalized_key == "traceparent":
                    self._add_traceparent(
                        occurrences,
                        child_value,
                        source_path=child_path,
                        method="traceparent",
                        precedence=precedence,
                        state=state,
                    )
                else:
                    identifier = self._identifier_keys.get(normalized_key)
                    if identifier is not None:
                        self._add_identifier(
                            occurrences,
                            identifier,
                            child_value,
                            source_path=child_path,
                            method=method,
                            precedence=precedence,
                            state=state,
                        )

                if normalized_key in self._target_service_keys:
                    self._add_hint(target_hints, child_value, precedence, state)
                if normalized_key in self._operation_hint_keys:
                    self._add_hint(operation_hints, child_value, precedence, state)

                self._walk_value(
                    child_value,
                    path=child_path,
                    root=root,
                    depth=depth + 1,
                    method=method,
                    occurrences=occurrences,
                    target_hints=target_hints,
                    operation_hints=operation_hints,
                    state=state,
                )
            return

        if isinstance(value, tuple | list):
            object_id = id(value)
            if object_id in state["seen"]:
                return
            state["seen"].add(object_id)

            for index, item in enumerate(value):
                if state["inspected"] >= self.max_inspected_values:
                    break
                self._walk_value(
                    item,
                    path=f"{path}[{index}]",
                    root=root,
                    depth=depth + 1,
                    method=method,
                    occurrences=occurrences,
                    target_hints=target_hints,
                    operation_hints=operation_hints,
                    state=state,
                )
            return

        if isinstance(value, str):
            self._inspect_string(
                value,
                path=path,
                root=root,
                depth=depth,
                occurrences=occurrences,
                target_hints=target_hints,
                operation_hints=operation_hints,
                state=state,
            )

    def _inspect_string(
        self,
        value: str,
        *,
        path: str,
        root: Literal["parameters", "metadata"],
        depth: int,
        occurrences: dict[TraceIdentifierName, list[_IdentifierOccurrence]],
        target_hints: list[_TargetHintOccurrence],
        operation_hints: list[_TargetHintOccurrence],
        state: dict[str, Any],
    ) -> None:
        stripped = value.strip()
        if not stripped or len(stripped) > self.max_string_length:
            return

        if self._looks_like_json(stripped):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, Mapping | list | tuple):
                self._walk_value(
                    parsed,
                    path=path,
                    root=root,
                    depth=depth + 1,
                    method="json_string",
                    occurrences=occurrences,
                    target_hints=target_hints,
                    operation_hints=operation_hints,
                    state=state,
                )

        self._extract_key_value_tokens(
            stripped,
            path=path,
            root=root,
            depth=depth,
            occurrences=occurrences,
            target_hints=target_hints,
            operation_hints=operation_hints,
            state=state,
        )

    def _extract_key_value_tokens(
        self,
        text: str,
        *,
        path: str,
        root: Literal["parameters", "metadata"],
        depth: int,
        occurrences: dict[TraceIdentifierName, list[_IdentifierOccurrence]],
        target_hints: list[_TargetHintOccurrence],
        operation_hints: list[_TargetHintOccurrence],
        state: dict[str, Any],
    ) -> None:
        precedence = self._precedence(root, depth, "key_value_text")
        for match in self._KEY_VALUE_PATTERN.finditer(text):
            if state["inspected"] >= self.max_inspected_values:
                break
            normalized_key = self._normalize_key(match.group("key"))
            token_path = self._join_path(path, match.group("key"))
            token_value = match.group("value")

            if normalized_key == "traceparent":
                self._add_traceparent(
                    occurrences,
                    token_value,
                    source_path=token_path,
                    method="traceparent",
                    precedence=precedence,
                    state=state,
                )
                continue

            identifier = self._identifier_keys.get(normalized_key)
            if identifier is not None:
                self._add_identifier(
                    occurrences,
                    identifier,
                    token_value,
                    source_path=token_path,
                    method="key_value_text",
                    precedence=precedence,
                    state=state,
                    from_text=True,
                )
                continue

            if normalized_key in self._target_service_keys:
                self._add_hint(
                    target_hints, token_value, precedence, state, from_text=True
                )
            if normalized_key in self._operation_hint_keys:
                self._add_hint(
                    operation_hints, token_value, precedence, state, from_text=True
                )

    def _add_traceparent(
        self,
        occurrences: dict[TraceIdentifierName, list[_IdentifierOccurrence]],
        value: Any,
        *,
        source_path: str,
        method: ExtractionMethod,
        precedence: int,
        state: dict[str, Any],
    ) -> None:
        traceparent = self._coerce_identifier_value(value)
        if traceparent is None:
            return

        parsed = self._parse_traceparent(traceparent)
        if parsed is None:
            return

        trace_id, parent_span_id = parsed
        self._add_identifier(
            occurrences,
            "trace_id",
            trace_id,
            source_path=f"{source_path}.trace_id",
            method=method,
            precedence=precedence,
            state=state,
        )
        self._add_identifier(
            occurrences,
            "parent_span_id",
            parent_span_id,
            source_path=f"{source_path}.parent_span_id",
            method=method,
            precedence=precedence,
            state=state,
        )

    def _add_identifier(
        self,
        occurrences: dict[TraceIdentifierName, list[_IdentifierOccurrence]],
        identifier: TraceIdentifierName,
        value: Any,
        *,
        source_path: str,
        method: ExtractionMethod,
        precedence: int,
        state: dict[str, Any],
        from_text: bool = False,
    ) -> None:
        coerced = self._coerce_identifier_value(value, from_text=from_text)
        if coerced is None:
            return

        occurrences[identifier].append(
            _IdentifierOccurrence(
                identifier=identifier,
                value=coerced,
                source_path=source_path,
                method=method,
                precedence=precedence,
                order=self._next_order(state),
            )
        )

    def _add_hint(
        self,
        hints: list[_TargetHintOccurrence],
        value: Any,
        precedence: int,
        state: dict[str, Any],
        *,
        from_text: bool = False,
    ) -> None:
        coerced = self._coerce_identifier_value(value, from_text=from_text)
        if coerced is None:
            return
        hints.append(
            _TargetHintOccurrence(
                value=coerced,
                precedence=precedence,
                order=self._next_order(state),
            )
        )

    def _parse_traceparent(self, value: str) -> tuple[str, str] | None:
        match = self._TRACEPARENT_PATTERN.match(value)
        if match is None:
            return None

        version = match.group("version").lower()
        trace_id = match.group("trace_id").lower()
        parent_id = match.group("parent_id").lower()

        if version == "ff":
            return None
        if trace_id == "0" * 32 or parent_id == "0" * 16:
            return None
        return trace_id, parent_id

    def _coerce_identifier_value(
        self, value: Any, *, from_text: bool = False
    ) -> str | None:
        if value is None or isinstance(value, bool | bytes | bytearray | memoryview):
            return None
        if isinstance(value, str):
            coerced = value
        elif isinstance(value, int | float):
            if from_text:
                return None
            coerced = str(value)
        else:
            return None

        coerced = coerced.strip().strip("\"'").strip()
        coerced = coerced.rstrip(".,;")
        if not coerced or len(coerced) > self.DEFAULT_MAX_IDENTIFIER_LENGTH:
            return None
        if from_text and coerced.isdecimal():
            return None
        return coerced

    def _canonical_transaction_id(
        self,
        values: dict[TraceIdentifierName, str | None],
    ) -> str | None:
        for identifier in (
            "trace_id",
            "correlation_id",
            "transaction_id",
            "request_id",
        ):
            value = values.get(identifier)
            if value:
                return value
        return None

    def _select_identifier(
        self,
        occurrences: list[_IdentifierOccurrence],
    ) -> _IdentifierOccurrence | None:
        if not occurrences:
            return None
        return sorted(
            occurrences,
            key=lambda occurrence: (occurrence.precedence, occurrence.order),
        )[0]

    def _select_hint(self, hints: list[_TargetHintOccurrence]) -> str | None:
        if not hints:
            return None
        return sorted(hints, key=lambda hint: (hint.precedence, hint.order))[0].value

    def _conflict_counts(
        self,
        occurrences: dict[TraceIdentifierName, list[_IdentifierOccurrence]],
        selected: dict[TraceIdentifierName, _IdentifierOccurrence | None],
    ) -> dict[TraceIdentifierName, int]:
        conflicts: dict[TraceIdentifierName, int] = {}
        for identifier, identifier_occurrences in occurrences.items():
            selected_occurrence = selected[identifier]
            if selected_occurrence is None:
                continue
            conflict_count = sum(
                1
                for occurrence in identifier_occurrences
                if occurrence.value != selected_occurrence.value
            )
            if conflict_count:
                conflicts[identifier] = conflict_count
        return conflicts

    def _precedence(
        self,
        root: Literal["parameters", "metadata"],
        depth: int,
        method: Literal["structured", "json_string", "key_value_text"],
    ) -> int:
        if method == "json_string":
            return 40
        if method == "key_value_text":
            return 50
        if root == "parameters" and depth <= 1:
            return 10
        if root == "metadata" and depth == 0:
            return 20
        return 30

    def _next_order(self, state: dict[str, Any]) -> int:
        order = int(state["order"])
        state["order"] = order + 1
        return order

    def _looks_like_json(self, value: str) -> bool:
        return (value.startswith("{") and value.endswith("}")) or (
            value.startswith("[") and value.endswith("]")
        )

    def _join_path(self, parent: str, key: str) -> str:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            return f"{parent}.{key}"
        return f"{parent}.{key}"

    def _normalize_key(self, key: str) -> str:
        return re.sub(r"[^a-z0-9]", "", key.lower())

    def _build_identifier_key_map(self) -> dict[str, TraceIdentifierName]:
        keys: dict[str, TraceIdentifierName] = {}
        for identifier, aliases in self._IDENTIFIER_ALIASES.items():
            for alias in aliases:
                keys[self._normalize_key(alias)] = identifier
        return keys

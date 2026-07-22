"""Bounded in-memory NetworkX topology pipeline for trace observations."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import networkx as nx

from .runtime_dependency_parser import TraceObservation


TransactionKey = tuple[str | None, str]


@dataclass(frozen=True)
class _StoredObservation:
    """Trace observation plus deterministic ingestion metadata."""

    observation: TraceObservation
    service: str
    timestamp: datetime
    insertion_order: int


@dataclass(frozen=True)
class _TransitionContribution:
    """One per-transaction service dependency contribution."""

    source: str
    target: str
    first_timestamp: datetime
    last_timestamp: datetime
    delay_ms: float | None
    evidence_types: frozenset[str]


class NetworkXTopologyPipeline:
    """Build baseline service topology from TraceObservation events.

    The pipeline stores bounded transaction-local observations and rebuilds the
    aggregate graph from retained state for every graph/snapshot request. This
    makes repeated reads idempotent and prevents incremental double-counting.

    Edge deduplication rule: within one transaction, a source-service to
    target-service transition contributes at most once to aggregate
    transition_count, even if both span-parent and temporal evidence identify
    the same logical transition. Evidence type counters record the evidence
    observed for that deduplicated contribution.
    """

    DEFAULT_MAX_TRANSACTIONS = 1000
    DEFAULT_MAX_OBSERVATIONS_PER_TRANSACTION = 500

    def __init__(
        self,
        *,
        max_transactions: int = DEFAULT_MAX_TRANSACTIONS,
        max_observations_per_transaction: int = DEFAULT_MAX_OBSERVATIONS_PER_TRANSACTION,
    ) -> None:
        if max_transactions <= 0:
            raise ValueError("max_transactions must be greater than 0")
        if max_observations_per_transaction <= 0:
            raise ValueError(
                "max_observations_per_transaction must be greater than 0"
            )

        self.max_transactions = max_transactions
        self.max_observations_per_transaction = max_observations_per_transaction
        self._transactions: OrderedDict[TransactionKey, list[_StoredObservation]] = (
            OrderedDict()
        )
        self._next_insertion_order = 0
        self.accepted_observation_count = 0
        self.rejected_observation_count = 0
        self.evicted_transaction_count = 0
        self.evicted_observation_count = 0

    def add_observation(self, observation: TraceObservation) -> bool:
        """Store one valid TraceObservation and return whether it was accepted."""
        transaction_id = self._clean_text(observation.canonical_transaction_id)
        service = self._clean_text(observation.service)
        timestamp = self._normalize_timestamp(observation.timestamp)
        if transaction_id is None or service is None or timestamp is None:
            self.rejected_observation_count += 1
            return False

        key = self._transaction_key(transaction_id, observation.environment)
        stored = _StoredObservation(
            observation=observation,
            service=service,
            timestamp=timestamp,
            insertion_order=self._next_insertion_order,
        )
        self._next_insertion_order += 1

        if key not in self._transactions:
            self._transactions[key] = []
        self._transactions[key].append(stored)
        self._transactions.move_to_end(key)

        if len(self._transactions[key]) > self.max_observations_per_transaction:
            overflow = len(self._transactions[key]) - self.max_observations_per_transaction
            del self._transactions[key][:overflow]
            self.evicted_observation_count += overflow

        while len(self._transactions) > self.max_transactions:
            _, evicted = self._transactions.popitem(last=False)
            self.evicted_transaction_count += 1
            self.evicted_observation_count += len(evicted)

        self.accepted_observation_count += 1
        return True

    def build_graph(self) -> nx.DiGraph:
        """Return a newly built directed graph from retained observations."""
        graph = nx.DiGraph()
        node_stats: dict[str, dict[str, Any]] = {}
        edge_stats: dict[tuple[str, str], dict[str, Any]] = {}
        latest_seen: datetime | None = None

        for key, observations in self._transactions.items():
            vector = self._transaction_vector_from_observations(observations)
            transaction_label = self._transaction_label(key)

            services_in_transaction: set[str] = set()
            for entry in vector:
                service = entry["service"]
                timestamp = entry["_timestamp"]
                latest_seen = self._max_datetime(latest_seen, timestamp)
                services_in_transaction.add(service)
                stats = node_stats.setdefault(
                    service,
                    {
                        "event_count": 0,
                        "transactions": set(),
                        "first_seen": timestamp,
                        "last_seen": timestamp,
                        "offsets": [],
                    },
                )
                stats["event_count"] += 1
                stats["transactions"].add(transaction_label)
                stats["first_seen"] = self._min_datetime(stats["first_seen"], timestamp)
                stats["last_seen"] = self._max_datetime(stats["last_seen"], timestamp)
                stats["offsets"].append(entry["start_offset_ms"])

            for contribution in self._derive_transaction_transitions(vector):
                edge_key = (contribution.source, contribution.target)
                stats = edge_stats.setdefault(
                    edge_key,
                    {
                        "transition_count": 0,
                        "transactions": set(),
                        "first_seen": contribution.first_timestamp,
                        "last_seen": contribution.last_timestamp,
                        "delays": [],
                        "span_evidence_count": 0,
                        "temporal_evidence_count": 0,
                    },
                )
                stats["transition_count"] += 1
                stats["transactions"].add(transaction_label)
                stats["first_seen"] = self._min_datetime(
                    stats["first_seen"], contribution.first_timestamp
                )
                stats["last_seen"] = self._max_datetime(
                    stats["last_seen"], contribution.last_timestamp
                )
                if contribution.delay_ms is not None:
                    stats["delays"].append(contribution.delay_ms)
                if "span" in contribution.evidence_types:
                    stats["span_evidence_count"] += 1
                if "temporal" in contribution.evidence_types:
                    stats["temporal_evidence_count"] += 1

        for service in sorted(node_stats):
            stats = node_stats[service]
            offsets = stats["offsets"]
            graph.add_node(
                service,
                service=service,
                event_count=stats["event_count"],
                transaction_count=len(stats["transactions"]),
                first_seen=stats["first_seen"],
                last_seen=stats["last_seen"],
                minimum_start_offset_ms=min(offsets),
                maximum_start_offset_ms=max(offsets),
                average_start_offset_ms=sum(offsets) / len(offsets),
            )

        for source, target in sorted(edge_stats):
            stats = edge_stats[(source, target)]
            delays = stats["delays"]
            graph.add_edge(
                source,
                target,
                source=source,
                target=target,
                transition_count=stats["transition_count"],
                transaction_count=len(stats["transactions"]),
                first_seen=stats["first_seen"],
                last_seen=stats["last_seen"],
                minimum_delay_ms=min(delays) if delays else None,
                maximum_delay_ms=max(delays) if delays else None,
                average_delay_ms=(sum(delays) / len(delays)) if delays else None,
                span_evidence_count=stats["span_evidence_count"],
                temporal_evidence_count=stats["temporal_evidence_count"],
            )

        graph.graph["transaction_count"] = len(self._transactions)
        graph.graph["generated_at"] = latest_seen
        return graph

    def get_snapshot(self) -> dict[str, Any]:
        """Return deterministic JSON-compatible topology data."""
        graph = self.build_graph()
        nodes = [
            self._serialize_attrs({"id": node, **attrs})
            for node, attrs in sorted(graph.nodes(data=True), key=lambda item: item[0])
        ]
        edges = [
            self._serialize_attrs(
                {
                    "id": f"{source}->{target}",
                    **attrs,
                }
            )
            for source, target, attrs in sorted(
                graph.edges(data=True), key=lambda item: (item[0], item[1])
            )
        ]
        generated_at = graph.graph.get("generated_at")
        return {
            "generated_at": self._serialize_datetime(generated_at),
            "node_count": graph.number_of_nodes(),
            "edge_count": graph.number_of_edges(),
            "transaction_count": graph.graph.get("transaction_count", 0),
            "nodes": nodes,
            "edges": edges,
        }

    def get_transaction_vector(
        self,
        transaction_id: str,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return one retained transaction's temporal start vector."""
        cleaned_transaction_id = self._clean_text(transaction_id)
        if cleaned_transaction_id is None:
            return []

        key = self._find_transaction_key(cleaned_transaction_id, environment)
        if key is None:
            return []

        vector = self._transaction_vector_from_observations(self._transactions[key])
        return [self._serialize_vector_entry(entry) for entry in vector]

    def get_stats(self) -> dict[str, Any]:
        """Return bounded-state and current topology statistics."""
        graph = self.build_graph()
        return {
            "active_transaction_count": len(self._transactions),
            "stored_observation_count": sum(
                len(observations) for observations in self._transactions.values()
            ),
            "accepted_observation_count": self.accepted_observation_count,
            "rejected_observation_count": self.rejected_observation_count,
            "evicted_transaction_count": self.evicted_transaction_count,
            "evicted_observation_count": self.evicted_observation_count,
            "current_node_count": graph.number_of_nodes(),
            "current_edge_count": graph.number_of_edges(),
        }

    def clear(self) -> None:
        """Clear retained topology state and counters."""
        self._transactions.clear()
        self._next_insertion_order = 0
        self.accepted_observation_count = 0
        self.rejected_observation_count = 0
        self.evicted_transaction_count = 0
        self.evicted_observation_count = 0

    def _transaction_key(
        self,
        transaction_id: str,
        environment: str | None,
    ) -> TransactionKey:
        return (self._clean_text(environment), transaction_id)

    def _find_transaction_key(
        self,
        transaction_id: str,
        environment: str | None,
    ) -> TransactionKey | None:
        if environment is not None:
            key = self._transaction_key(transaction_id, environment)
            return key if key in self._transactions else None

        exact_key = (None, transaction_id)
        if exact_key in self._transactions:
            return exact_key

        matches = [key for key in self._transactions if key[1] == transaction_id]
        return matches[0] if len(matches) == 1 else None

    def _transaction_label(self, key: TransactionKey) -> str:
        environment, transaction_id = key
        return f"{environment}\x1f{transaction_id}" if environment else transaction_id

    def _transaction_vector_from_observations(
        self,
        observations: list[_StoredObservation],
    ) -> list[dict[str, Any]]:
        if not observations:
            return []

        ordered = sorted(
            observations,
            key=lambda stored: (stored.timestamp, stored.insertion_order),
        )
        start_timestamp = ordered[0].timestamp
        vector: list[dict[str, Any]] = []
        for stored in ordered:
            offset_ms = max(
                0.0,
                (stored.timestamp - start_timestamp).total_seconds() * 1000,
            )
            vector.append(
                {
                    "service": stored.service,
                    "timestamp": self._serialize_datetime(stored.timestamp),
                    "_timestamp": stored.timestamp,
                    "start_offset_ms": offset_ms,
                    "span_id": stored.observation.span_id,
                    "parent_span_id": stored.observation.parent_span_id,
                    "template_id": stored.observation.template_id,
                    "insertion_order": stored.insertion_order,
                    "_observation": stored.observation,
                }
            )
        return vector

    def _derive_transaction_transitions(
        self,
        vector: list[dict[str, Any]],
    ) -> list[_TransitionContribution]:
        contributions: dict[tuple[str, str], _TransitionContribution] = {}
        span_connected_orders: set[int] = set()

        span_index: dict[str, list[dict[str, Any]]] = {}
        for entry in vector:
            span_id = self._clean_text(entry["span_id"])
            if span_id is not None:
                span_index.setdefault(span_id, []).append(entry)

        for child in vector:
            parent_span_id = self._clean_text(child["parent_span_id"])
            if parent_span_id is None:
                continue
            parents = span_index.get(parent_span_id, [])
            if len(parents) != 1:
                continue
            parent = parents[0]
            if parent["service"] == child["service"]:
                continue

            delay_ms = self._delay_ms(parent["_timestamp"], child["_timestamp"])
            contribution = _TransitionContribution(
                source=parent["service"],
                target=child["service"],
                first_timestamp=min(parent["_timestamp"], child["_timestamp"]),
                last_timestamp=max(parent["_timestamp"], child["_timestamp"]),
                delay_ms=delay_ms,
                evidence_types=frozenset({"span"}),
            )
            self._merge_contribution(contributions, contribution)
            span_connected_orders.add(parent["insertion_order"])
            span_connected_orders.add(child["insertion_order"])

        for source, target in zip(vector, vector[1:]):
            if source["service"] == target["service"]:
                continue

            contribution = _TransitionContribution(
                source=source["service"],
                target=target["service"],
                first_timestamp=source["_timestamp"],
                last_timestamp=target["_timestamp"],
                delay_ms=self._delay_ms(source["_timestamp"], target["_timestamp"]),
                evidence_types=frozenset({"temporal"}),
            )
            existing = contributions.get((contribution.source, contribution.target))
            if existing and "span" in existing.evidence_types:
                self._merge_contribution(contributions, contribution)
                continue
            if (
                source["insertion_order"] in span_connected_orders
                and target["insertion_order"] in span_connected_orders
            ):
                continue
            self._merge_contribution(contributions, contribution)

        return [
            contributions[key]
            for key in sorted(contributions, key=lambda edge: (edge[0], edge[1]))
        ]

    def _merge_contribution(
        self,
        contributions: dict[tuple[str, str], _TransitionContribution],
        contribution: _TransitionContribution,
    ) -> None:
        key = (contribution.source, contribution.target)
        existing = contributions.get(key)
        if existing is None:
            contributions[key] = contribution
            return

        delay_ms = existing.delay_ms
        if delay_ms is None and contribution.delay_ms is not None:
            delay_ms = contribution.delay_ms

        contributions[key] = _TransitionContribution(
            source=existing.source,
            target=existing.target,
            first_timestamp=min(existing.first_timestamp, contribution.first_timestamp),
            last_timestamp=max(existing.last_timestamp, contribution.last_timestamp),
            delay_ms=delay_ms,
            evidence_types=existing.evidence_types | contribution.evidence_types,
        )

    def _delay_ms(self, source_timestamp: datetime, target_timestamp: datetime) -> float | None:
        delay_ms = (target_timestamp - source_timestamp).total_seconds() * 1000
        return delay_ms if delay_ms >= 0 else None

    def _normalize_timestamp(self, timestamp: Any) -> datetime | None:
        if isinstance(timestamp, datetime):
            normalized = timestamp
        elif isinstance(timestamp, str):
            try:
                normalized = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None

        if normalized.tzinfo is None:
            return normalized.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc)

    def _clean_text(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    def _min_datetime(self, left: datetime, right: datetime) -> datetime:
        return left if left <= right else right

    def _max_datetime(
        self,
        left: datetime | None,
        right: datetime,
    ) -> datetime:
        return right if left is None or right > left else left

    def _serialize_vector_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": entry["service"],
            "timestamp": entry["timestamp"],
            "start_offset_ms": entry["start_offset_ms"],
            "span_id": entry["span_id"],
            "parent_span_id": entry["parent_span_id"],
            "template_id": entry["template_id"],
            "insertion_order": entry["insertion_order"],
        }

    def _serialize_attrs(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {
            key: self._serialize_datetime(value) if isinstance(value, datetime) else value
            for key, value in attrs.items()
        }

    def _serialize_datetime(self, value: Any) -> str | None:
        if not isinstance(value, datetime):
            return None
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

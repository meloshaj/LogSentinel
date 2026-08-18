"""Deterministic graph pathway scoring and blast-radius analysis."""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..schemas.blast_radius import (
    BlastRadiusNode,
    BlastRadiusResult,
    PathwayComponentScores,
    PathwayScore,
    RootCauseCandidate,
    ServiceAnomalyEvidence,
)
from ..schemas.alerting import IncidentAlertPayload
from .alerting import dispatch_incident_alert


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(converted) or converted < 0:
        return None
    return converted


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})


class PathwayScoreWeights(BaseModel):
    """Configurable weighted-sum components for pathway scoring."""

    root_anomaly_score: float = Field(default=0.20, ge=0.0)
    affected_service_anomaly_score: float = Field(default=0.20, ge=0.0)
    temporal_proximity: float = Field(default=0.20, ge=0.0)
    trace_overlap: float = Field(default=0.15, ge=0.0)
    edge_strength: float = Field(default=0.15, ge=0.0)
    hop_proximity: float = Field(default=0.10, ge=0.0)
    symptom_consistency: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(validate_assignment=True)

    @model_validator(mode="after")
    def _validate_positive_total(self) -> "PathwayScoreWeights":
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("at least one pathway score weight must be positive")
        return self

    def normalized(self) -> dict[str, float]:
        values = self.model_dump()
        total = sum(values.values())
        return {key: value / total for key, value in values.items()}


class RootAggregationWeights(BaseModel):
    """Configurable root-cause aggregation weights."""

    average_pathway_score: float = Field(default=0.45, ge=0.0)
    coverage_ratio: float = Field(default=0.20, ge=0.0)
    direct_root_anomaly_score: float = Field(default=0.15, ge=0.0)
    strongest_pathway_score: float = Field(default=0.10, ge=0.0)
    shared_trace_coverage: float = Field(default=0.10, ge=0.0)

    model_config = ConfigDict(validate_assignment=True)

    @model_validator(mode="after")
    def _validate_positive_total(self) -> "RootAggregationWeights":
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("at least one root aggregation weight must be positive")
        return self

    def normalized(self) -> dict[str, float]:
        values = self.model_dump()
        total = sum(values.values())
        return {key: value / total for key, value in values.items()}


class DynamicGraphScorerConfig(BaseModel):
    """Configuration for deterministic graph pathway scoring.

    Temporal proximity uses ``exp(-delta / temporal_decay_seconds)`` when root
    evidence occurs before or at the affected service. Root evidence after the
    symptom is multiplied by ``future_evidence_penalty`` and decayed by the
    absolute time gap, making future root evidence possible but strongly
    disfavored. Missing direct root evidence receives temporal proximity 0.0.
    """

    pathway_weights: PathwayScoreWeights = Field(default_factory=PathwayScoreWeights)
    root_aggregation_weights: RootAggregationWeights = Field(
        default_factory=RootAggregationWeights
    )
    hop_decay: float = Field(default=0.75, gt=0.0, le=1.0)
    temporal_decay_seconds: float = Field(default=300.0, gt=0.0)
    future_evidence_penalty: float = Field(default=0.05, ge=0.0, le=1.0)
    maximum_depth: int = Field(default=5, gt=0)
    minimum_pathway_score_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    minimum_blast_radius_impact_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    neutral_edge_strength: float = Field(default=0.50, ge=0.0, le=1.0)
    algorithm_version: str = Field(default="dynamic-graph-pathway-scorer-v1")

    model_config = ConfigDict(validate_assignment=True)


@dataclass(frozen=True)
class _EvidenceSummary:
    service_name: str
    anomaly_score: float
    severity_score: float
    observed_at: datetime
    correlation_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    error_count: int | None
    warning_count: int | None

    @property
    def symptom_score(self) -> float:
        return _clamp((self.anomaly_score + self.severity_score) / 2.0)


class DynamicGraphPathwayScorer:
    """Score root-cause candidates and blast radius over a caller-to-callee graph."""

    def __init__(self, config: DynamicGraphScorerConfig | None = None) -> None:
        self.config = config or DynamicGraphScorerConfig()

    def score(
        self,
        graph: nx.DiGraph,
        evidence: Sequence[ServiceAnomalyEvidence],
        *,
        calculated_at: datetime | None = None,
    ) -> BlastRadiusResult:
        """Coordinate root ranking, pathway scoring and blast-radius calculation."""
        if not isinstance(graph, nx.DiGraph):
            raise TypeError("graph must be a networkx.DiGraph")

        calculated = _normalize_datetime(calculated_at or _utc_now())
        summaries = self._summarize_evidence(evidence)
        if not summaries:
            return self._empty_result(calculated)

        candidates = self.rank_root_candidates(graph, summaries)
        if not candidates:
            return self._empty_result(calculated)

        winner = candidates[0]
        blast_radius = self.calculate_blast_radius(graph, winner, summaries)
        directly_affected = sum(
            1 for node in blast_radius if node.impact_classification == "direct"
        )
        indirectly_affected = sum(
            1 for node in blast_radius if node.impact_classification == "indirect"
        )
        aggregate_score = (
            _clamp(sum(node.impact_score for node in blast_radius) / len(blast_radius))
            if blast_radius
            else 0.0
        )
        pathways = sorted(
            winner.supporting_pathways,
            key=lambda item: (
                item.candidate_root_service,
                item.affected_service,
                item.dependency_path,
            ),
        )

        result = BlastRadiusResult(
            suspected_root_service=winner.service_name,
            root_cause_score=winner.root_cause_score,
            confidence=self._calculate_confidence(candidates),
            ranked_root_cause_candidates=candidates,
            affected_services=[item.service_name for item in blast_radius],
            scored_propagation_pathways=pathways,
            blast_radius=blast_radius,
            directly_affected_service_count=directly_affected,
            indirectly_affected_service_count=indirectly_affected,
            total_blast_radius_services=len(blast_radius),
            aggregate_blast_radius_score=aggregate_score,
            supporting_event_ids=winner.supporting_event_ids,
            supporting_correlation_ids=winner.supporting_correlation_ids,
            calculated_at=calculated,
            algorithm_version=self.config.algorithm_version,
        )
        
        # Trigger alert if confidence is high
        if result.confidence >= 0.7:
            payload = IncidentAlertPayload(
                incident_id=winner.supporting_event_ids[0] if winner.supporting_event_ids else str(uuid.uuid4()),
                root_cause_service=winner.service_name,
                triggering_template=f"Score: {winner.root_cause_score:.2f}",
                affected_services=result.affected_services,
                propagation_chain=[item.service_name for item in blast_radius],
                confidence_score=result.confidence,
                is_critical=(result.confidence >= 0.9 or directly_affected > 2)
            )
            
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(dispatch_incident_alert(payload))
            except RuntimeError:
                # No running loop, can't easily dispatch async from sync here
                pass
                
        return result

    def rank_root_candidates(
        self,
        graph: nx.DiGraph,
        evidence_by_service: Mapping[str, _EvidenceSummary]
        | Sequence[ServiceAnomalyEvidence],
    ) -> list[RootCauseCandidate]:
        """Return deterministically sorted root-cause candidates."""
        summaries = (
            self._summarize_evidence(evidence_by_service)
            if not isinstance(evidence_by_service, Mapping)
            else dict(evidence_by_service)
        )
        if not summaries:
            return []

        candidate_names = self._discover_candidate_roots(graph, summaries)
        all_correlation_ids = set().union(
            *(set(summary.correlation_ids) for summary in summaries.values())
        )
        total_anomalous = len(summaries)
        root_weights = self.config.root_aggregation_weights.normalized()
        ranked: list[RootCauseCandidate] = []

        for candidate in candidate_names:
            kept_pathways: list[PathwayScore] = []
            for affected in sorted(summaries):
                pathway = self.score_pathway(graph, candidate, affected, summaries)
                if (
                    pathway is not None
                    and pathway.final_score
                    >= self.config.minimum_pathway_score_threshold
                ):
                    kept_pathways.append(pathway)

            if not kept_pathways:
                continue

            average_pathway = sum(item.final_score for item in kept_pathways) / len(
                kept_pathways
            )
            strongest_pathway = max(item.final_score for item in kept_pathways)
            coverage_ratio = len({item.affected_service for item in kept_pathways}) / max(
                total_anomalous, 1
            )
            direct_root_anomaly = summaries.get(candidate)
            direct_root_score = direct_root_anomaly.anomaly_score if direct_root_anomaly else 0.0
            supporting_correlation_ids = set().union(
                *(set(item.supporting_correlation_ids) for item in kept_pathways)
            )
            trace_coverage = (
                len(supporting_correlation_ids & all_correlation_ids)
                / len(all_correlation_ids)
                if all_correlation_ids
                else 0.0
            )
            root_score = _clamp(
                root_weights["average_pathway_score"] * average_pathway
                + root_weights["coverage_ratio"] * coverage_ratio
                + root_weights["direct_root_anomaly_score"] * direct_root_score
                + root_weights["strongest_pathway_score"] * strongest_pathway
                + root_weights["shared_trace_coverage"] * trace_coverage
            )
            supporting_event_ids = set().union(
                *(set(item.supporting_event_ids) for item in kept_pathways)
            )

            ranked.append(
                RootCauseCandidate(
                    service_name=candidate,
                    root_cause_score=root_score,
                    explained_service_count=len(
                        {item.affected_service for item in kept_pathways}
                    ),
                    total_anomalous_services_considered=total_anomalous,
                    coverage_ratio=_clamp(coverage_ratio),
                    supporting_pathways=sorted(
                        kept_pathways,
                        key=lambda item: (
                            item.affected_service,
                            item.dependency_path,
                        ),
                    ),
                    supporting_event_ids=_sorted_unique(supporting_event_ids),
                    supporting_correlation_ids=_sorted_unique(supporting_correlation_ids),
                )
            )

        return sorted(
            ranked,
            key=lambda item: (
                -item.root_cause_score,
                -item.coverage_ratio,
                -max(
                    (pathway.final_score for pathway in item.supporting_pathways),
                    default=0.0,
                ),
                item.service_name,
            ),
        )

    def score_pathway(
        self,
        graph: nx.DiGraph,
        candidate_root: str,
        affected_service: str,
        evidence_by_service: Mapping[str, _EvidenceSummary]
        | Sequence[ServiceAnomalyEvidence],
    ) -> PathwayScore | None:
        """Score the best dependency path from affected service to candidate root."""
        summaries = (
            self._summarize_evidence(evidence_by_service)
            if not isinstance(evidence_by_service, Mapping)
            else dict(evidence_by_service)
        )
        affected = summaries.get(affected_service)
        if affected is None:
            return None

        dependency_path = self.find_best_dependency_path(
            graph,
            affected_service,
            candidate_root,
        )
        if dependency_path is None:
            return None

        root = summaries.get(candidate_root)
        hop_count = len(dependency_path) - 1
        propagation_path = list(reversed(dependency_path))
        root_anomaly = root.anomaly_score if root is not None else 0.0
        temporal = self.temporal_proximity(root, affected)
        trace_overlap = self.trace_overlap(
            root.correlation_ids if root is not None else (),
            affected.correlation_ids,
        )
        edge_strength = self.path_edge_strength(graph, dependency_path)
        hop_proximity = _clamp(self.config.hop_decay**hop_count)
        symptom_consistency = self._symptom_consistency(root, affected, hop_count)

        components = PathwayComponentScores(
            root_anomaly_score=root_anomaly,
            affected_service_anomaly_score=affected.symptom_score,
            temporal_proximity=temporal,
            trace_overlap=trace_overlap,
            edge_strength=edge_strength,
            hop_proximity=hop_proximity,
            symptom_consistency=symptom_consistency,
        )
        final_score = self._weighted_pathway_score(components)
        shared_correlation_ids = (
            set(root.correlation_ids) & set(affected.correlation_ids)
            if root is not None
            else set()
        )
        event_ids = set(affected.event_ids)
        if root is not None:
            event_ids.update(root.event_ids)

        return PathwayScore(
            candidate_root_service=candidate_root,
            affected_service=affected_service,
            dependency_path=dependency_path,
            propagation_path=propagation_path,
            hop_count=hop_count,
            component_scores=components,
            final_score=final_score,
            supporting_correlation_ids=_sorted_unique(shared_correlation_ids),
            supporting_event_ids=_sorted_unique(event_ids),
            reasons=[
                f"dependency path uses caller-to-callee edges: {' -> '.join(dependency_path)}",
                f"failure propagation path is reverse traversal: {' -> '.join(propagation_path)}",
            ],
        )

    def calculate_blast_radius(
        self,
        graph: nx.DiGraph,
        winning_candidate: RootCauseCandidate,
        evidence_by_service: Mapping[str, _EvidenceSummary]
        | Sequence[ServiceAnomalyEvidence],
    ) -> list[BlastRadiusNode]:
        """Calculate impacted upstream callers by traversing graph predecessors."""
        summaries = (
            self._summarize_evidence(evidence_by_service)
            if not isinstance(evidence_by_service, Mapping)
            else dict(evidence_by_service)
        )
        root = winning_candidate.service_name
        impacted = {root: 0}

        if root in graph:
            lengths = nx.single_source_shortest_path_length(
                graph.reverse(copy=False),
                root,
                cutoff=self.config.maximum_depth,
            )
            impacted.update({service: depth for service, depth in lengths.items()})

        pathway_by_service = {
            pathway.affected_service: pathway
            for pathway in winning_candidate.supporting_pathways
        }
        nodes: list[BlastRadiusNode] = []
        for service in sorted(impacted):
            dependency_path = self.find_best_dependency_path(graph, service, root)
            if dependency_path is None and service == root:
                dependency_path = [root]
            if dependency_path is None:
                continue

            hop_count = len(dependency_path) - 1
            propagation_path = list(reversed(dependency_path))
            edge_strength = self.path_edge_strength(graph, dependency_path)
            evidence = summaries.get(service)
            evidence_score = evidence.symptom_score if evidence is not None else 0.0
            pathway_score = pathway_by_service.get(service)
            impact_score = _clamp(
                winning_candidate.root_cause_score
                * (self.config.hop_decay**hop_count)
                * edge_strength
                * (
                    0.70
                    + 0.20 * evidence_score
                    + 0.10 * (pathway_score.final_score if pathway_score else 0.0)
                )
            )
            if (
                service != root
                and impact_score
                < self.config.minimum_blast_radius_impact_threshold
            ):
                continue

            classification = (
                "root" if hop_count == 0 else "direct" if hop_count == 1 else "indirect"
            )
            supporting_evidence: dict[str, Any] = {}
            if evidence is not None:
                supporting_evidence = {
                    "anomaly_score": evidence.anomaly_score,
                    "severity_score": evidence.severity_score,
                    "correlation_ids": list(evidence.correlation_ids),
                    "event_ids": list(evidence.event_ids),
                    "error_count": evidence.error_count,
                    "warning_count": evidence.warning_count,
                }

            nodes.append(
                BlastRadiusNode(
                    service_name=service,
                    hop_distance=hop_count,
                    impact_classification=classification,
                    dependency_path=dependency_path,
                    propagation_path=propagation_path,
                    impact_score=impact_score,
                    edge_strength_score=edge_strength,
                    supporting_evidence=supporting_evidence,
                )
            )

        return sorted(
            nodes,
            key=lambda item: (
                item.hop_distance,
                item.service_name,
                item.dependency_path,
            ),
        )

    def find_best_dependency_path(
        self,
        graph: nx.DiGraph,
        affected_service: str,
        candidate_root: str,
    ) -> list[str] | None:
        """Find affected -> ... -> root path using strongest edges, then hops, then lexicographic order."""
        if affected_service == candidate_root:
            return [affected_service]
        if affected_service not in graph or candidate_root not in graph:
            return None

        try:
            paths = list(
                nx.all_simple_paths(
                    graph,
                    affected_service,
                    candidate_root,
                    cutoff=self.config.maximum_depth,
                )
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

        if not paths:
            return None
        scored_paths = [
            (self.path_edge_strength(graph, path), len(path) - 1, tuple(path), path)
            for path in paths
            if len(path) - 1 <= self.config.maximum_depth
        ]
        if not scored_paths:
            return None
        return list(
            sorted(scored_paths, key=lambda item: (-item[0], item[1], item[2]))[0][3]
        )

    def trace_overlap(self, left: Iterable[str], right: Iterable[str]) -> float:
        """Return deterministic Jaccard similarity for correlation IDs."""
        left_set = set(left)
        right_set = set(right)
        if not left_set or not right_set:
            return 0.0
        return _clamp(len(left_set & right_set) / len(left_set | right_set))

    def temporal_proximity(
        self,
        root: _EvidenceSummary | None,
        affected: _EvidenceSummary,
    ) -> float:
        """Return temporal decay; future root evidence is strongly penalized."""
        if root is None:
            return 0.0
        delta_seconds = (affected.observed_at - root.observed_at).total_seconds()
        if delta_seconds == 0:
            return 1.0
        if delta_seconds > 0:
            return _clamp(math.exp(-delta_seconds / self.config.temporal_decay_seconds))
        future_lag_seconds = abs(delta_seconds)
        return _clamp(
            self.config.future_evidence_penalty
            * math.exp(-future_lag_seconds / self.config.temporal_decay_seconds)
        )

    def edge_strength(self, graph: nx.DiGraph, caller: str, callee: str) -> float:
        """Normalize an edge's transition_count against outgoing edges from the same caller."""
        if not graph.has_edge(caller, callee):
            return self.config.neutral_edge_strength

        edge_count = _safe_float(graph.edges[caller, callee].get("transition_count"))
        if edge_count is None:
            return self.config.neutral_edge_strength

        outgoing_counts = [
            count
            for _, target in graph.out_edges(caller)
            for count in [_safe_float(graph.edges[caller, target].get("transition_count"))]
            if count is not None
        ]
        if not outgoing_counts:
            return self.config.neutral_edge_strength
        max_count = max(outgoing_counts)
        if max_count <= 0:
            return self.config.neutral_edge_strength
        return _clamp(edge_count / max_count)

    def path_edge_strength(self, graph: nx.DiGraph, path: Sequence[str]) -> float:
        """Aggregate path edge strengths by geometric mean."""
        if len(path) <= 1:
            return 1.0
        strengths = [
            self.edge_strength(graph, caller, callee)
            for caller, callee in zip(path, path[1:])
        ]
        if not strengths:
            return 1.0
        product = 1.0
        for strength in strengths:
            product *= max(strength, 0.0)
        return _clamp(product ** (1.0 / len(strengths)))

    def _weighted_pathway_score(self, components: PathwayComponentScores) -> float:
        weights = self.config.pathway_weights.normalized()
        values = components.model_dump()
        score = 0.0
        for key, weight in weights.items():
            component_value = values.get(key)
            score += weight * (component_value if component_value is not None else 0.0)
        return _clamp(score)

    def _discover_candidate_roots(
        self,
        graph: nx.DiGraph,
        evidence_by_service: Mapping[str, _EvidenceSummary],
    ) -> list[str]:
        candidates: set[str] = set()
        for service in sorted(evidence_by_service):
            candidates.add(service)
            if service not in graph:
                continue
            lengths = nx.single_source_shortest_path_length(
                graph,
                service,
                cutoff=self.config.maximum_depth,
            )
            candidates.update(lengths.keys())
        return sorted(candidates)

    def _calculate_confidence(self, candidates: Sequence[RootCauseCandidate]) -> float:
        if not candidates:
            return 0.0
        winner = candidates[0]
        average_pathway = (
            sum(pathway.final_score for pathway in winner.supporting_pathways)
            / len(winner.supporting_pathways)
            if winner.supporting_pathways
            else 0.0
        )
        if len(candidates) == 1:
            margin = 1.0
        else:
            second = candidates[1].root_cause_score
            margin = (winner.root_cause_score - second) / max(winner.root_cause_score, 1e-12)
        return _clamp(
            0.40 * _clamp(margin)
            + 0.35 * winner.coverage_ratio
            + 0.25 * average_pathway
        )

    def _summarize_evidence(
        self,
        evidence: Sequence[ServiceAnomalyEvidence] | Mapping[str, _EvidenceSummary],
    ) -> dict[str, _EvidenceSummary]:
        if isinstance(evidence, Mapping):
            return dict(evidence)

        grouped: dict[str, list[ServiceAnomalyEvidence]] = {}
        for item in evidence:
            grouped.setdefault(item.service_name, []).append(item.model_copy(deep=True))

        summaries: dict[str, _EvidenceSummary] = {}
        for service in sorted(grouped):
            items = sorted(
                grouped[service],
                key=lambda item: (
                    item.observed_at,
                    item.service_name,
                    tuple(item.event_ids),
                    tuple(item.correlation_ids),
                ),
            )
            summaries[service] = _EvidenceSummary(
                service_name=service,
                anomaly_score=_clamp(max(item.anomaly_score for item in items)),
                severity_score=_clamp(max(item.severity_score for item in items)),
                observed_at=min(item.observed_at for item in items),
                correlation_ids=tuple(
                    _sorted_unique(
                        correlation_id
                        for item in items
                        for correlation_id in item.correlation_ids
                    )
                ),
                event_ids=tuple(
                    _sorted_unique(event_id for item in items for event_id in item.event_ids)
                ),
                error_count=self._sum_optional_counts(item.error_count for item in items),
                warning_count=self._sum_optional_counts(
                    item.warning_count for item in items
                ),
            )
        return summaries

    def _sum_optional_counts(self, values: Iterable[int | None]) -> int | None:
        present = [value for value in values if value is not None]
        return sum(present) if present else None

    def _symptom_consistency(
        self,
        root: _EvidenceSummary | None,
        affected: _EvidenceSummary,
        hop_count: int,
    ) -> float | None:
        # Reserved for future deterministic cascade metadata scoring. The current
        # public evidence summary intentionally avoids exposing arbitrary metadata.
        return None

    def _empty_result(self, calculated_at: datetime) -> BlastRadiusResult:
        return BlastRadiusResult(
            suspected_root_service=None,
            root_cause_score=0.0,
            confidence=0.0,
            ranked_root_cause_candidates=[],
            affected_services=[],
            scored_propagation_pathways=[],
            blast_radius=[],
            directly_affected_service_count=0,
            indirectly_affected_service_count=0,
            total_blast_radius_services=0,
            aggregate_blast_radius_score=0.0,
            supporting_event_ids=[],
            supporting_correlation_ids=[],
            calculated_at=calculated_at,
            algorithm_version=self.config.algorithm_version,
        )

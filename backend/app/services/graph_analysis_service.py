"""Runtime adapter from anomaly pipeline data to graph scorer evidence."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.settings import GraphScoringSettings
from ..ml.anomaly_scoring import normalize_prediction_anomaly_score
from ..models import FeatureVector
from ..repositories.feature_repository import FeatureRepository
from ..repositories.log_repository import LogRepository
from ..schemas.blast_radius import BlastRadiusResult, ServiceAnomalyEvidence
from .graph_scorer import DynamicGraphPathwayScorer
from .topology_pipeline import NetworkXTopologyPipeline

logger = logging.getLogger("logsentinel.graph_analysis")

SEVERITY_SCORE_BY_LABEL: dict[str, float] = {
    "normal": 0.0,
    "info": 0.0,
    "low": 0.25,
    "medium": 0.60,
    "high": 0.85,
    "critical": 1.0,
}
UNKNOWN_SEVERITY_SCORE = 0.50


@dataclass
class _ServiceEvidenceAccumulator:
    service_name: str
    anomaly_score: float = 0.0
    severity_score: float = 0.0
    observed_at: datetime | None = None
    correlation_ids: set[str] = field(default_factory=set)
    event_ids: set[str] = field(default_factory=set)
    error_count: int = 0
    warning_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_anomaly(
        self,
        *,
        anomaly_score: float,
        severity_score: float,
        observed_at: datetime,
        event_id: str,
    ) -> None:
        self.anomaly_score = max(self.anomaly_score, anomaly_score)
        self.severity_score = max(self.severity_score, severity_score)
        if self.observed_at is None or observed_at < self.observed_at:
            self.observed_at = observed_at
        if event_id:
            self.event_ids.add(event_id)

    def to_schema(self) -> ServiceAnomalyEvidence:
        return ServiceAnomalyEvidence(
            service_name=self.service_name,
            anomaly_score=self.anomaly_score,
            severity_score=self.severity_score,
            observed_at=self.observed_at or datetime.now(timezone.utc),
            correlation_ids=sorted(self.correlation_ids),
            event_ids=sorted(self.event_ids),
            error_count=self.error_count,
            warning_count=self.warning_count,
            metadata=dict(self.metadata),
        )


class GraphAnalysisService:
    """Collect runtime evidence and invoke graph pathway scoring."""

    def __init__(
        self,
        *,
        topology_pipeline: NetworkXTopologyPipeline,
        feature_repository: FeatureRepository,
        log_repository: LogRepository,
        scorer: DynamicGraphPathwayScorer | None = None,
        settings: GraphScoringSettings | None = None,
    ) -> None:
        self.topology_pipeline = topology_pipeline
        self.feature_repository = feature_repository
        self.log_repository = log_repository
        self.scorer = scorer or DynamicGraphPathwayScorer()
        self.settings = settings or GraphScoringSettings()

    async def analyze_anomaly(
        self,
        *,
        anomaly_event: Mapping[str, Any] | None = None,
        feature_vector: FeatureVector,
        calculated_at: datetime | None = None,
    ) -> BlastRadiusResult | None:
        """Analyze one runtime anomaly without mutating topology or pipeline state."""
        if not self.settings.enabled:
            logger.debug("Graph analysis skipped: disabled")
            return None

        started = time.perf_counter()
        graph = self.topology_pipeline.get_graph_copy()
        if graph.number_of_nodes() == 0:
            logger.debug("Graph analysis skipped: empty topology graph")
            return None

        observed_at = _feature_observed_at(feature_vector)
        calculated = _normalize_datetime(calculated_at or observed_at)
        lookback_start = observed_at - timedelta(seconds=self.settings.lookback_seconds)
        anomaly_contexts = await self.feature_repository.get_recent_anomaly_contexts(
            start_time=lookback_start,
            end_time=observed_at,
            limit=self.settings.max_anomaly_events,
        )
        current_context = _context_from_feature_vector(feature_vector, anomaly_event)
        contexts = _dedupe_contexts([current_context, *anomaly_contexts])
        evidence = await self.build_evidence(
            contexts=contexts,
            start_time=lookback_start,
            end_time=observed_at,
        )
        if not evidence:
            logger.debug("Graph analysis skipped: no anomaly evidence")
            return None

        result = self.scorer.score(graph, evidence, calculated_at=calculated)
        if result.suspected_root_service is None:
            logger.debug("Graph analysis skipped: scorer returned no suspected root")
            return None

        logger.debug(
            "Graph analysis succeeded: services=%d nodes=%d edges=%d duration_ms=%.2f",
            len(evidence),
            graph.number_of_nodes(),
            graph.number_of_edges(),
            (time.perf_counter() - started) * 1000.0,
        )
        return result

    async def build_evidence(
        self,
        *,
        contexts: Sequence[Mapping[str, Any]],
        start_time: datetime,
        end_time: datetime,
    ) -> list[ServiceAnomalyEvidence]:
        """Build one normalized evidence record per service."""
        accumulators: dict[str, _ServiceEvidenceAccumulator] = {}
        service_names: set[str] = set()

        for context in contexts:
            prediction = _prediction_from_context(context)
            raw_score = prediction.get("anomaly_score", context.get("score"))
            severity = prediction.get("severity", context.get("severity", "unknown"))
            normalized_score = normalize_anomaly_score(
                raw_score,
                is_anomaly=prediction.get("is_anomaly"),
                raw_score=prediction.get("raw_score"),
            )
            severity_score = severity_to_score(severity)
            observed_at = _context_observed_at(context)
            event_id = _context_event_id(context)
            services = _services_from_context(context)
            for service in services:
                service_names.add(service)
                accumulator = accumulators.setdefault(
                    service,
                    _ServiceEvidenceAccumulator(service_name=service),
                )
                accumulator.add_anomaly(
                    anomaly_score=normalized_score,
                    severity_score=severity_score,
                    observed_at=observed_at,
                    event_id=event_id,
                )

        if not accumulators:
            return []

        correlation_ids = _correlation_ids_from_contexts(contexts)
        log_rows = await self.log_repository.get_recent_correlation_evidence(
            start_time=start_time,
            end_time=end_time,
            services=sorted(service_names),
            correlation_ids=correlation_ids,
            limit=self.settings.max_log_records,
        )
        self._apply_log_evidence(accumulators, log_rows)

        return [
            accumulators[service].to_schema()
            for service in sorted(accumulators)
        ]

    def _apply_log_evidence(
        self,
        accumulators: dict[str, _ServiceEvidenceAccumulator],
        log_rows: Sequence[Mapping[str, Any]],
    ) -> None:
        for row in log_rows:
            service = _clean_text(row.get("service"))
            if service is None or service not in accumulators:
                continue
            correlation_id = _clean_text(row.get("correlation_id"))
            if correlation_id is not None:
                accumulators[service].correlation_ids.add(correlation_id)
            level = _clean_text(row.get("level"))
            if level is None:
                normalized_level = ""
            else:
                normalized_level = level.lower()
            if normalized_level == "error":
                accumulators[service].error_count += 1
            elif normalized_level in {"warning", "warn"}:
                accumulators[service].warning_count += 1
            # Removed root_cause and propagated_symptom trust from arbitrary metadata
            pass


def normalize_anomaly_score(
    value: Any,
    *,
    is_anomaly: Any = None,
    raw_score: Any = None,
) -> float:
    """Normalize anomaly evidence, preferring the explicit detector contract."""
    prediction: dict[str, Any] = {"anomaly_score": value}
    if is_anomaly is not None:
        prediction["is_anomaly"] = is_anomaly
    if raw_score is not None:
        prediction["raw_score"] = raw_score
    return normalize_prediction_anomaly_score(prediction)


def severity_to_score(value: Any) -> float:
    label = _clean_text(value)
    if label is None:
        return UNKNOWN_SEVERITY_SCORE
    return SEVERITY_SCORE_BY_LABEL.get(label.lower(), UNKNOWN_SEVERITY_SCORE)


def _context_from_feature_vector(
    feature_vector: FeatureVector,
    anomaly_event: Mapping[str, Any] | None,
) -> dict[str, Any]:
    prediction = dict(feature_vector.anomaly_prediction or {})
    context = {
        "anomaly_event_id": None,
        "window_id": feature_vector.window_id,
        "severity": prediction.get("severity", "unknown"),
        "score": prediction.get("anomaly_score"),
        "details": prediction,
        "anomaly_created_at": _feature_observed_at(feature_vector),
        "start_time": feature_vector.window_start,
        "end_time": feature_vector.window_end,
        "service": None,
        "log_count": feature_vector.log_count,
        "feature_vector": _feature_payload(feature_vector),
        "anomaly_prediction": prediction,
    }
    if anomaly_event:
        context.update(dict(anomaly_event))
    return context


def _feature_payload(feature_vector: FeatureVector) -> dict[str, Any]:
    return {
        "service_distribution": dict(feature_vector.service_distribution),
        "error_count": feature_vector.error_count,
        "warning_count": feature_vector.warning_count,
        **dict(feature_vector.features),
    }


def _dedupe_contexts(contexts: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[Mapping[str, Any]] = []
    for context in contexts:
        key = (
            str(context.get("anomaly_event_id") or ""),
            str(context.get("window_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(context)
    return sorted(deduped, key=lambda item: (_context_observed_at(item), _context_event_id(item)))


def _prediction_from_context(context: Mapping[str, Any]) -> Mapping[str, Any]:
    prediction = context.get("anomaly_prediction")
    if isinstance(prediction, Mapping):
        return prediction
    details = context.get("details")
    if isinstance(details, Mapping):
        return details
    return {}


def _services_from_context(context: Mapping[str, Any]) -> list[str]:
    service = _clean_text(context.get("service"))
    if service is not None:
        return [service]

    feature_vector = context.get("feature_vector")
    if isinstance(feature_vector, Mapping):
        distribution = feature_vector.get("service_distribution")
        if isinstance(distribution, Mapping):
            services = sorted(
                service
                for service in (_clean_text(value) for value in distribution.keys())
                if service is not None
            )
            if services:
                return services
        dominant = _clean_text(feature_vector.get("dominant_service"))
        if dominant is not None:
            return [dominant]

    details = context.get("details")
    if isinstance(details, Mapping):
        detail_service = _clean_text(details.get("service"))
        if detail_service is not None:
            return [detail_service]

    return []


def _context_observed_at(context: Mapping[str, Any]) -> datetime:
    for key in ("end_time", "anomaly_created_at", "created_at", "start_time"):
        value = context.get(key)
        if isinstance(value, datetime):
            return _normalize_datetime(value)
    return datetime.now(timezone.utc)


def _feature_observed_at(feature_vector: FeatureVector) -> datetime:
    return _normalize_datetime(
        feature_vector.window_end
        or feature_vector.window_start
        or feature_vector.timestamp
    )


def _context_event_id(context: Mapping[str, Any]) -> str:
    anomaly_id = context.get("anomaly_event_id")
    if anomaly_id is not None:
        return f"anomaly:{anomaly_id}"
    window_id = context.get("window_id")
    return f"window:{window_id}" if window_id else ""


def _correlation_ids_from_contexts(contexts: Sequence[Mapping[str, Any]]) -> list[str]:
    values: set[str] = set()
    for context in contexts:
        _collect_correlation_values(context, values)
        for key in ("details", "anomaly_prediction", "feature_vector"):
            nested = context.get(key)
            if isinstance(nested, Mapping):
                _collect_correlation_values(nested, values)
    return sorted(values)


def _collect_correlation_values(source: Mapping[str, Any], values: set[str]) -> None:
    for key in ("correlation_id", "correlation_ids"):
        raw_value = source.get(key)
        if isinstance(raw_value, str):
            cleaned = _clean_text(raw_value)
            if cleaned is not None:
                values.add(cleaned)
        elif isinstance(raw_value, Sequence) and not isinstance(raw_value, str | bytes | bytearray):
            for item in raw_value:
                cleaned = _clean_text(item)
                if cleaned is not None:
                    values.add(cleaned)


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    if converted != converted or converted in (float("inf"), float("-inf")):
        return None
    return converted


def _clamp(value: float) -> float:
    if value != value or value in (float("inf"), float("-inf")):
        return 0.0
    return max(0.0, min(1.0, value))


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

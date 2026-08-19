"""Strict REST contracts for runtime topology and blast-radius retrieval."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .blast_radius import BlastRadiusResult

TopologyNodeType = Literal["gateway", "service", "database", "cache", "queue"]
TopologyNodeStatus = Literal["healthy", "degraded", "critical"]


class TopologyNodeMetrics(BaseModel):
    latency_p95_ms: float = Field(ge=0.0)
    error_rate_pct: float = Field(ge=0.0, le=100.0)
    throughput_rps: float = Field(ge=0.0)


class TopologyNodeContract(BaseModel):
    id: str = Field(pattern=r"^svc_[a-z0-9_]+$")
    name: str = Field(min_length=1)
    type: TopologyNodeType
    status: TopologyNodeStatus
    metrics: TopologyNodeMetrics
    active_anomaly_id: str | None = None
    is_root_cause: bool = False


class TopologyEdgeContract(BaseModel):
    id: str = Field(pattern=r"^edge_[a-z0-9_]+_to_[a-z0-9_]+$")
    source: str = Field(pattern=r"^svc_[a-z0-9_]+$")
    target: str = Field(pattern=r"^svc_[a-z0-9_]+$")
    call_count: int = Field(ge=0)
    avg_latency_ms: float = Field(ge=0.0)
    error_count: int = Field(ge=0)
    is_blast_path: bool = False


class TopologyResponse(BaseModel):
    nodes: list[TopologyNodeContract] = Field(default_factory=list)
    edges: list[TopologyEdgeContract] = Field(default_factory=list)
    snapshot_timestamp: datetime = Field(alias="generated_at")

    model_config = ConfigDict(validate_assignment=True, populate_by_name=True)

    @field_validator("snapshot_timestamp")
    @classmethod
    def _normalize_snapshot_timestamp(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class BlastRadiusRetrievalResponse(BaseModel):
    tracking_loop_id: int
    analysis_available: bool
    blast_radius: BlastRadiusResult | None
    suspected_root_service: str | None = None
    root_cause_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    graph_analysis_version: str | None = None
    triggered_at: datetime | None = None

    model_config = ConfigDict(validate_assignment=True)

    @field_validator("triggered_at")
    @classmethod
    def _normalize_triggered_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def topology_service_id(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"svc_{normalized or 'unknown'}"


def runtime_topology_contract(
    snapshot: Mapping[str, Any], active_tracking_loops: Iterable[Mapping[str, Any]] = ()
) -> TopologyResponse:
    """Convert NetworkX runtime output into the public stable topology contract."""
    incident_nodes, blast_edges = _active_incident_state(active_tracking_loops)
    raw_nodes = [entry for entry in snapshot.get("nodes", []) if isinstance(entry, Mapping)]
    raw_edges = [entry for entry in snapshot.get("edges", []) if isinstance(entry, Mapping)]
    nodes_by_name = {
        name: entry
        for entry in raw_nodes
        if (name := _text(entry.get("service")) or _text(entry.get("name")) or _text(entry.get("id")))
    }

    nodes = [
        _node_contract(name, raw, incident_nodes.get(name))
        for name, raw in sorted(nodes_by_name.items())
    ]
    edges: list[TopologyEdgeContract] = []
    for raw in raw_edges:
        source_name, target_name = _text(raw.get("source")), _text(raw.get("target"))
        if source_name is None or target_name is None:
            continue
        source, target = topology_service_id(source_name), topology_service_id(target_name)
        edges.append(
            TopologyEdgeContract(
                id=f"edge_{source.removeprefix('svc_')}_to_{target.removeprefix('svc_')}",
                source=source,
                target=target,
                call_count=int(_number(raw.get("transition_count"))),
                avg_latency_ms=_number(raw.get("average_delay_ms")),
                error_count=int(_number(raw.get("error_count"))),
                is_blast_path=(source_name, target_name) in blast_edges,
            )
        )
    return TopologyResponse(
        nodes=nodes,
        edges=edges,
        snapshot_timestamp=_timestamp(snapshot.get("generated_at")),
    )


def _node_contract(
    name: str, raw: Mapping[str, Any], incident_state: Mapping[str, Any] | None
) -> TopologyNodeContract:
    status: TopologyNodeStatus = "healthy"
    anomaly_id: str | None = None
    root = False
    if incident_state is not None:
        status = incident_state["status"]
        anomaly_id = incident_state["anomaly_id"]
        root = incident_state["root"]
    return TopologyNodeContract(
        id=topology_service_id(name),
        name=name,
        type=_node_type(name),
        status=status,
        metrics=TopologyNodeMetrics(
            latency_p95_ms=_number(raw.get("average_start_offset_ms")),
            error_rate_pct=0.0,
            throughput_rps=_number(raw.get("event_count")) / max(_number(raw.get("transaction_count")), 1.0),
        ),
        active_anomaly_id=anomaly_id,
        is_root_cause=root,
    )


def _active_incident_state(
    loops: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str]]]:
    nodes: dict[str, dict[str, Any]] = {}
    blast_edges: set[tuple[str, str]] = set()
    for loop in loops:
        anomaly_id = _text(loop.get("window_id")) or _text(loop.get("id"))
        root_name = _text(loop.get("suspected_root_service"))
        entries = loop.get("blast_radius")
        if isinstance(entries, Mapping):
            entries = entries.get("blast_radius")
        if not isinstance(entries, list):
            entries = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            name = _text(entry.get("service_name"))
            if name is None:
                continue
            root = name == root_name or entry.get("impact_classification") == "root"
            nodes[name] = {"status": "critical" if root else "degraded", "anomaly_id": anomaly_id, "root": root}
            path = entry.get("propagation_path")
            if isinstance(path, list):
                names = [item for item in path if isinstance(item, str) and item]
                blast_edges.update(zip(names, names[1:]))
        if root_name and root_name not in nodes:
            nodes[root_name] = {"status": "critical", "anomaly_id": anomaly_id, "root": True}
    return nodes, blast_edges


def _node_type(name: str) -> TopologyNodeType:
    value = name.lower()
    if "gateway" in value or "ingress" in value:
        return "gateway"
    if any(token in value for token in ("postgres", "mysql", "mongo", "database", "-db", "_db")):
        return "database"
    if any(token in value for token in ("redis", "cache", "memcached")):
        return "cache"
    if any(token in value for token in ("queue", "kafka", "rabbit")):
        return "queue"
    return "service"


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number >= 0 else 0.0


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)

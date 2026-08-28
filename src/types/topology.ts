// ---------------------------------------------------------------------------
// Topology data contracts — mirrors backend TopologyResponse schema with
// strict frontend typing and safe defaults for untyped backend dicts.
// ---------------------------------------------------------------------------

export type NodeType = "gateway" | "service" | "database" | "cache" | "queue";

export type NodeStatus = "healthy" | "degraded" | "critical";

export interface TopologyNodeMetrics {
  latency_p95_ms: number;
  error_rate_pct: number;
  throughput_rps: number;
}

export interface TopologyNode {
  id: string;
  name: string;
  type: NodeType;
  status: NodeStatus;
  metrics: TopologyNodeMetrics;
  active_anomaly_id: string | null;
  is_root_cause: boolean;
}

export interface TopologyEdge {
  id: string;
  source: string;
  target: string;
  call_count: number;
  avg_latency_ms: number;
  error_count: number;
  is_blast_path: boolean;
}

export interface TopologyResponse {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  snapshot_timestamp: string;
}

/**
 * Dynamic overlay state applied imperatively to nodes without triggering
 * React re-renders on the static graph layer.
 */
export interface NodeOverlayState {
  nodeId: string;
  status: NodeStatus;
  anomalyScore?: number;
  blastRadiusRoot?: boolean;
}

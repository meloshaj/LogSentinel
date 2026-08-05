// ---------------------------------------------------------------------------
// Topology data contracts — mirrors backend TopologyResponse schema with
// strict frontend typing and safe defaults for untyped backend dicts.
// ---------------------------------------------------------------------------

export type NodeType = "service" | "database" | "cache" | "queue" | "gateway";

export type NodeStatus = "healthy" | "degraded" | "critical";

export type EdgeStatus = "normal" | "stressed" | "failing";

export interface TopologyNode {
  id: string;
  label: string;
  type: NodeType;
  status: NodeStatus;
  metadata?: Record<string, unknown>;
}

export interface TopologyEdge {
  id: string;
  source: string;
  target: string;
  latency_ms?: number;
  error_rate?: number;
  status?: EdgeStatus;
}

export interface TopologyResponse {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  updated_at: string;
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

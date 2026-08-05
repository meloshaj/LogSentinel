import { useState, useEffect, useCallback, useRef } from "react";
import type {
  TopologyNode,
  TopologyEdge,
  NodeType,
  NodeStatus,
  EdgeStatus,
} from "../types/topology";

// Legacy exports for dev components
export interface LegacyTopologyNode {
  id: string;
  service: string;
  event_count: number;
  transaction_count: number;
  first_seen: string;
  last_seen: string;
  minimum_start_offset_ms: number;
  maximum_start_offset_ms: number;
  average_start_offset_ms: number;
}

export interface LegacyTopologyEdge {
  id: string;
  source: string;
  target: string;
  transition_count: number;
  transaction_count: number;
  first_seen: string;
  last_seen: string;
  minimum_delay_ms: number | null;
  maximum_delay_ms: number | null;
  average_delay_ms: number | null;
  span_evidence_count: number;
  temporal_evidence_count: number;
  target_hint_evidence_count: number;
}

export interface TopologyPayload {
  generated_at: string | null;
  node_count: number;
  edge_count: number;
  transaction_count: number;
  nodes: TopologyNode[] | LegacyTopologyNode[] | any[];
  edges: TopologyEdge[] | LegacyTopologyEdge[] | any[];
}

const POLL_INTERVAL_MS = 30_000;
const TOPOLOGY_PATH = "/api/v1/topology";
const FALLBACK_URL = "http://localhost:8000/api/v1/topology";

function buildTopologyUrls(): string[] {
  const candidates = [
    import.meta.env.VITE_API_URL
      ? `${import.meta.env.VITE_API_URL}${TOPOLOGY_PATH}`
      : null,
    `${window.location.protocol}//${window.location.host}${TOPOLOGY_PATH}`,
    FALLBACK_URL,
  ];
  return candidates.filter((c): c is string => Boolean(c));
}

const VALID_NODE_TYPES = new Set<NodeType>(["service", "database", "cache", "queue", "gateway"]);
const VALID_NODE_STATUSES = new Set<NodeStatus>(["healthy", "degraded", "critical"]);
const VALID_EDGE_STATUSES = new Set<EdgeStatus>(["normal", "stressed", "failing"]);

function mapNode(raw: Record<string, unknown>, index: number): TopologyNode {
  const id = typeof raw.id === "string" && raw.id ? raw.id : typeof raw.name === "string" && raw.name ? raw.name : `node-${index}`;
  const label = typeof raw.label === "string" && raw.label ? raw.label : typeof raw.name === "string" && raw.name ? raw.name : id;
  const rawType = typeof raw.type === "string" ? raw.type.toLowerCase() : "";
  const type: NodeType = VALID_NODE_TYPES.has(rawType as NodeType) ? (rawType as NodeType) : "service";
  const rawStatus = typeof raw.status === "string" ? raw.status.toLowerCase() : "";
  const status: NodeStatus = VALID_NODE_STATUSES.has(rawStatus as NodeStatus) ? (rawStatus as NodeStatus) : "healthy";

  return {
    id,
    label,
    type,
    status,
    metadata: typeof raw.metadata === "object" && raw.metadata !== null && !Array.isArray(raw.metadata) ? (raw.metadata as Record<string, unknown>) : undefined,
  };
}

function mapEdge(raw: Record<string, unknown>, index: number): TopologyEdge | null {
  const source = typeof raw.source === "string" ? raw.source : typeof raw.from === "string" ? raw.from : typeof raw.caller === "string" ? raw.caller : null;
  const target = typeof raw.target === "string" ? raw.target : typeof raw.to === "string" ? raw.to : typeof raw.callee === "string" ? raw.callee : null;
  if (!source || !target) return null;
  const id = typeof raw.id === "string" && raw.id ? raw.id : `edge-${source}-${target}-${index}`;
  const rawStatus = typeof raw.status === "string" ? raw.status.toLowerCase() : "";
  const status: EdgeStatus | undefined = VALID_EDGE_STATUSES.has(rawStatus as EdgeStatus) ? (rawStatus as EdgeStatus) : undefined;

  return {
    id,
    source,
    target,
    latency_ms: typeof raw.latency_ms === "number" ? raw.latency_ms : undefined,
    error_rate: typeof raw.error_rate === "number" ? raw.error_rate : undefined,
    status,
  };
}

export interface UseTopologyResult {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  topology: TopologyPayload | null;
  updatedAt: string | null;
  isLoading: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useTopology(pollIntervalMs = POLL_INTERVAL_MS): UseTopologyResult {
  const [nodes, setNodes] = useState<TopologyNode[]>([]);
  const [edges, setEdges] = useState<TopologyEdge[]>([]);
  const [topology, setTopology] = useState<TopologyPayload | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetchTopology = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setIsLoading(true);
    setError(null);

    const urls = buildTopologyUrls();
    let lastErr: Error | null = null;

    for (const url of urls) {
      if (controller.signal.aborted) return;
      try {
        const headers: Record<string, string> = {};
        const apiKey = import.meta.env.VITE_API_KEY as string | undefined;
        if (apiKey) headers["X-API-Key"] = apiKey;

        const res = await fetch(url, { signal: controller.signal, headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);

        const data = (await res.json()) as TopologyPayload;
        
        const rawNodes = Array.isArray(data.nodes) ? data.nodes : [];
        const rawEdges = Array.isArray(data.edges) ? data.edges : [];

        const mappedNodes = rawNodes.filter((n: unknown): n is Record<string, unknown> => typeof n === "object" && n !== null).map((n, i) => mapNode(n, i));
        const mappedEdges = rawEdges.filter((e: unknown): e is Record<string, unknown> => typeof e === "object" && e !== null).map((e, i) => mapEdge(e, i)).filter((e): e is TopologyEdge => e !== null);

        if (!controller.signal.aborted) {
          setNodes(mappedNodes);
          setEdges(mappedEdges);
          setTopology(data);
          setUpdatedAt(typeof data.generated_at === "string" ? data.generated_at : new Date().toISOString());
          setIsLoading(false);
        }
        return;
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        lastErr = err instanceof Error ? err : new Error(String(err));
      }
    }

    if (!controller.signal.aborted) {
      setError(lastErr?.message ?? "All topology URLs failed");
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTopology();
    const timer = setInterval(fetchTopology, pollIntervalMs);
    return () => {
      clearInterval(timer);
      abortRef.current?.abort();
    };
  }, [fetchTopology, pollIntervalMs]);

  return { nodes, edges, topology, updatedAt, isLoading, loading: isLoading, error, refresh: fetchTopology };
}

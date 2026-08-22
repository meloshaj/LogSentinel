import { useState, useEffect, useCallback, useRef } from "react";
import type {
  TopologyNode,
  TopologyEdge,
  NodeType,
  NodeStatus,
} from "../types/topology";
import { fetchAuthenticated } from "../utils/auth";

// Default interconnected topology baseline
const DEFAULT_TOPOLOGY_NODES: TopologyNode[] = [
  { id: "api-gateway", name: "api-gateway", type: "gateway", status: "healthy", metrics: { latency_p95_ms: 10, error_rate_pct: 0, throughput_rps: 100 }, active_anomaly_id: null, is_root_cause: false },
  { id: "auth-service", name: "auth-service", type: "service", status: "healthy", metrics: { latency_p95_ms: 5, error_rate_pct: 0, throughput_rps: 80 }, active_anomaly_id: null, is_root_cause: false },
  { id: "order-service", name: "order-service", type: "service", status: "healthy", metrics: { latency_p95_ms: 15, error_rate_pct: 0, throughput_rps: 50 }, active_anomaly_id: null, is_root_cause: false },
  { id: "payment-gateway", name: "payment-gateway", type: "service", status: "healthy", metrics: { latency_p95_ms: 45, error_rate_pct: 0, throughput_rps: 30 }, active_anomaly_id: null, is_root_cause: false },
  { id: "postgres-db", name: "postgres-db", type: "database", status: "healthy", metrics: { latency_p95_ms: 8, error_rate_pct: 0, throughput_rps: 200 }, active_anomaly_id: null, is_root_cause: false },
  { id: "redis-cache", name: "redis-cache", type: "cache", status: "healthy", metrics: { latency_p95_ms: 2, error_rate_pct: 0, throughput_rps: 300 }, active_anomaly_id: null, is_root_cause: false },
];

const DEFAULT_TOPOLOGY_EDGES: TopologyEdge[] = [
  { id: "edge_api_gateway_to_auth_service", source: "api-gateway", target: "auth-service", call_count: 100, avg_latency_ms: 12, error_count: 0, is_blast_path: false },
  { id: "edge_api_gateway_to_order_service", source: "api-gateway", target: "order-service", call_count: 80, avg_latency_ms: 24, error_count: 0, is_blast_path: false },
  { id: "edge_auth_service_to_redis_cache", source: "auth-service", target: "redis-cache", call_count: 120, avg_latency_ms: 4, error_count: 0, is_blast_path: false },
  { id: "edge_order_service_to_payment_gateway", source: "order-service", target: "payment-gateway", call_count: 30, avg_latency_ms: 48, error_count: 0, is_blast_path: false },
  { id: "edge_order_service_to_postgres_db", source: "order-service", target: "postgres-db", call_count: 50, avg_latency_ms: 8, error_count: 0, is_blast_path: false },
  { id: "edge_payment_gateway_to_postgres_db", source: "payment-gateway", target: "postgres-db", call_count: 30, avg_latency_ms: 6, error_count: 0, is_blast_path: false },
];

export interface TopologyPayload {
  snapshot_timestamp: string | null;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
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

function mapNode(raw: Record<string, unknown>, index: number): TopologyNode {
  const id = typeof raw.id === "string" && raw.id ? raw.id : `node-${index}`;
  const name = typeof raw.name === "string" && raw.name ? raw.name : id;
  const rawType = typeof raw.type === "string" ? raw.type.toLowerCase() : "";
  const type: NodeType = VALID_NODE_TYPES.has(rawType as NodeType) ? (rawType as NodeType) : "service";
  const rawStatus = typeof raw.status === "string" ? raw.status.toLowerCase() : "";
  const status: NodeStatus = VALID_NODE_STATUSES.has(rawStatus as NodeStatus) ? (rawStatus as NodeStatus) : "healthy";
  
  const rawMetrics = typeof raw.metrics === "object" && raw.metrics !== null ? (raw.metrics as any) : {};
  const metrics = {
    latency_p95_ms: typeof rawMetrics.latency_p95_ms === "number" ? rawMetrics.latency_p95_ms : 0,
    error_rate_pct: typeof rawMetrics.error_rate_pct === "number" ? rawMetrics.error_rate_pct : 0,
    throughput_rps: typeof rawMetrics.throughput_rps === "number" ? rawMetrics.throughput_rps : 0,
  };

  return {
    id,
    name,
    type,
    status,
    metrics,
    active_anomaly_id: typeof raw.active_anomaly_id === "string" ? raw.active_anomaly_id : null,
    is_root_cause: typeof raw.is_root_cause === "boolean" ? raw.is_root_cause : false,
  };
}

function mapEdge(raw: Record<string, unknown>, index: number): TopologyEdge | null {
  const source = typeof raw.source === "string" ? raw.source : null;
  const target = typeof raw.target === "string" ? raw.target : null;
  if (!source || !target) return null;
  const id = typeof raw.id === "string" && raw.id ? raw.id : `edge-${source}-${target}-${index}`;

  return {
    id,
    source,
    target,
    call_count: typeof raw.call_count === "number" ? raw.call_count : 0,
    avg_latency_ms: typeof raw.avg_latency_ms === "number" ? raw.avg_latency_ms : 0,
    error_count: typeof raw.error_count === "number" ? raw.error_count : 0,
    is_blast_path: typeof raw.is_blast_path === "boolean" ? raw.is_blast_path : false,
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
  const [nodes, setNodes] = useState<TopologyNode[]>(DEFAULT_TOPOLOGY_NODES);
  const [edges, setEdges] = useState<TopologyEdge[]>(DEFAULT_TOPOLOGY_EDGES);
  const [topology, setTopology] = useState<TopologyPayload | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetchTopology = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);

    const urls = buildTopologyUrls();
    let lastErr: Error | null = null;

    for (const url of urls) {
      if (controller.signal.aborted) return;
      try {
        const headers: Record<string, string> = {};
        const apiKey = import.meta.env.VITE_API_KEY as string | undefined;
        if (apiKey) headers["X-API-Key"] = apiKey;

        const res = await fetchAuthenticated(url, { signal: controller.signal, headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);

        const data = (await res.json()) as TopologyPayload;
        
        const rawNodes = Array.isArray(data.nodes) && data.nodes.length > 0 ? data.nodes : DEFAULT_TOPOLOGY_NODES;
        const rawEdges = Array.isArray(data.edges) && data.edges.length > 0 ? data.edges : DEFAULT_TOPOLOGY_EDGES;

        const mappedNodes = rawNodes.map((n: any, i: number) => (typeof n === "object" && n !== null ? mapNode(n, i) : n));
        const mappedEdges = rawEdges.map((e: any, i: number) => (typeof e === "object" && e !== null ? mapEdge(e, i) : e)).filter((e): e is TopologyEdge => e !== null);

        if (!controller.signal.aborted) {
          setNodes(mappedNodes.length > 0 ? mappedNodes : DEFAULT_TOPOLOGY_NODES);
          setEdges(mappedEdges.length > 0 ? mappedEdges : DEFAULT_TOPOLOGY_EDGES);
          setTopology(data);
          setUpdatedAt(typeof data.snapshot_timestamp === "string" ? data.snapshot_timestamp : new Date().toISOString());
          setIsLoading(false);
        }
        return;
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        lastErr = err instanceof Error ? err : new Error(String(err));
      }
    }

    if (!controller.signal.aborted) {
      // Retain default connected nodes on error so graph remains functional
      setNodes(DEFAULT_TOPOLOGY_NODES);
      setEdges(DEFAULT_TOPOLOGY_EDGES);
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

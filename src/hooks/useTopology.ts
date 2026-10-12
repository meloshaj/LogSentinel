import { useState, useEffect, useCallback, useRef } from "react";
import type {
  TopologyNode,
  TopologyEdge,
  NodeType,
  NodeStatus,
  TopologyNodeMetrics,
} from "../types/topology";
import { fetchAuthenticated } from "../utils/auth";
import {
  loadingState,
  staleState,
  successState,
  unavailableState,
  type OperationalDataState,
} from "../types/operational";

export interface TopologyPayload {
  snapshot_timestamp: string | null;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

const POLL_INTERVAL_MS = 30_000;
const TOPOLOGY_PATH = "/api/v1/topology";
const EMPTY_TOPOLOGY_NODES: TopologyNode[] = [];
const EMPTY_TOPOLOGY_EDGES: TopologyEdge[] = [];

function buildTopologyUrls(): string[] {
  const apiUrl = import.meta.env.VITE_API_URL || "";
  return [`${apiUrl.replace(/\/$/, "")}${TOPOLOGY_PATH}`];
}

const VALID_NODE_TYPES = new Set<NodeType>(["service", "database", "cache", "queue", "gateway"]);
const VALID_NODE_STATUSES = new Set<NodeStatus>(["healthy", "degraded", "critical"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function mapMetrics(raw: unknown): TopologyNodeMetrics | null {
  if (!isRecord(raw)) return null;
  if (!finiteNumber(raw.latency_p95_ms) || !finiteNumber(raw.error_rate_pct) || !finiteNumber(raw.throughput_rps)) {
    return null;
  }
  if (raw.error_rate_pct > 100) return null;
  return {
    latency_p95_ms: raw.latency_p95_ms,
    error_rate_pct: raw.error_rate_pct,
    throughput_rps: raw.throughput_rps,
  };
}

function mapNode(raw: Record<string, unknown>): TopologyNode | null {
  const id = typeof raw.id === "string" && raw.id ? raw.id : null;
  const name = typeof raw.name === "string" && raw.name ? raw.name : null;
  const rawType = typeof raw.type === "string" ? raw.type.toLowerCase() : null;
  const rawStatus = typeof raw.status === "string" ? raw.status.toLowerCase() : null;
  const metrics = mapMetrics(raw.metrics);
  if (
    !id ||
    !name ||
    !rawType ||
    !VALID_NODE_TYPES.has(rawType as NodeType) ||
    !rawStatus ||
    !VALID_NODE_STATUSES.has(rawStatus as NodeStatus) ||
    !metrics
  ) {
    return null;
  }

  return {
    id,
    name,
    type: rawType as NodeType,
    status: rawStatus as NodeStatus,
    metrics,
    active_anomaly_id: typeof raw.active_anomaly_id === "string" ? raw.active_anomaly_id : null,
    is_root_cause: typeof raw.is_root_cause === "boolean" ? raw.is_root_cause : false,
  };
}

function mapEdge(raw: Record<string, unknown>): TopologyEdge | null {
  if (
    typeof raw.id !== "string" ||
    !raw.id ||
    typeof raw.source !== "string" ||
    !raw.source ||
    typeof raw.target !== "string" ||
    !raw.target ||
    !finiteNumber(raw.call_count) ||
    !finiteNumber(raw.avg_latency_ms) ||
    !finiteNumber(raw.error_count)
  ) {
    return null;
  }
  return {
    id: raw.id,
    source: raw.source,
    target: raw.target,
    call_count: raw.call_count,
    avg_latency_ms: raw.avg_latency_ms,
    error_count: raw.error_count,
    is_blast_path: typeof raw.is_blast_path === "boolean" ? raw.is_blast_path : false,
  };
}

function parseSnapshotTimestamp(data: Record<string, unknown>): string | null {
  const raw = data.snapshot_timestamp ?? data.generated_at;
  if (typeof raw !== "string" || !Number.isFinite(new Date(raw).getTime())) return null;
  return new Date(raw).toISOString();
}

function parseTopology(value: unknown): TopologyPayload {
  if (!isRecord(value) || !Array.isArray(value.nodes) || !Array.isArray(value.edges)) {
    throw new Error("Topology response schema invalid");
  }
  const snapshotTimestamp = parseSnapshotTimestamp(value);
  if (!snapshotTimestamp) throw new Error("Topology response timestamp unavailable");

  const nodes: TopologyNode[] = [];
  for (const raw of value.nodes) {
    if (!isRecord(raw)) throw new Error("Topology node schema invalid");
    const node = mapNode(raw);
    if (!node) throw new Error("Topology node metrics or status unavailable");
    nodes.push(node);
  }

  const edges: TopologyEdge[] = [];
  for (const raw of value.edges) {
    if (!isRecord(raw)) throw new Error("Topology edge schema invalid");
    const edge = mapEdge(raw);
    if (!edge) throw new Error("Topology edge metrics unavailable");
    edges.push(edge);
  }
  return { snapshot_timestamp: snapshotTimestamp, nodes, edges };
}

export interface UseTopologyResult {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  topology: TopologyPayload | null;
  updatedAt: string | null;
  isLoading: boolean;
  loading: boolean;
  error: string | null;
  dataState: OperationalDataState<TopologyPayload>;
  refresh: () => void;
}

export function useTopology(pollIntervalMs = POLL_INTERVAL_MS): UseTopologyResult {
  const [nodes, setNodes] = useState<TopologyNode[]>(EMPTY_TOPOLOGY_NODES);
  const [edges, setEdges] = useState<TopologyEdge[]>(EMPTY_TOPOLOGY_EDGES);
  const [topology, setTopology] = useState<TopologyPayload | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dataState, setDataState] = useState<OperationalDataState<TopologyPayload>>(
    loadingState<TopologyPayload>("GET /api/v1/topology"),
  );
  const topologyRef = useRef<TopologyPayload | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetchTopology = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setIsLoading(true);

    try {
      const url = buildTopologyUrls()[0];
      const headers: Record<string, string> = {};
      const apiKey = import.meta.env.VITE_API_KEY as string | undefined;
      if (apiKey) headers["X-API-Key"] = apiKey;
      const response = await fetchAuthenticated(url, { signal: controller.signal, headers });
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      const parsed = parseTopology(await response.json());
      if (controller.signal.aborted) return;

      topologyRef.current = parsed;
      setNodes(parsed.nodes);
      setEdges(parsed.edges);
      setTopology(parsed);
      setUpdatedAt(parsed.snapshot_timestamp);
      setError(null);
      setDataState(successState(parsed, parsed.snapshot_timestamp ?? new Date().toISOString(), "GET /api/v1/topology", parsed.nodes.length === 0));
    } catch (caught) {
      if (controller.signal.aborted) return;
      const message = caught instanceof Error && caught.message.startsWith("HTTP 403")
        ? "Topology is not available for this scope"
        : "Topology is currently unavailable";
      setError(message);
      const previous = topologyRef.current;
      if (previous && previous.snapshot_timestamp) {
        setDataState(staleState(previous, previous.snapshot_timestamp, "GET /api/v1/topology", message));
      } else {
        setDataState(unavailableState("GET /api/v1/topology", message));
      }
    } finally {
      if (!controller.signal.aborted) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchTopology();
    const timer = setInterval(() => void fetchTopology(), pollIntervalMs);
    return () => {
      clearInterval(timer);
      abortRef.current?.abort();
    };
  }, [fetchTopology, pollIntervalMs]);

  return {
    nodes,
    edges,
    topology,
    updatedAt,
    isLoading,
    loading: isLoading,
    error,
    dataState,
    refresh: () => void fetchTopology(),
  };
}

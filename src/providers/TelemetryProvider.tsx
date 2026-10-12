/**
 * TelemetryProvider — Global telemetry context that maintains a single persistent
 * WebSocket connection, performs historical log backfill via REST, and reconciles
 * both streams into a deduplicated, chronologically sorted rolling buffer.
 *
 * ## Architecture
 *
 * On mount the provider:
 * 1. Opens the WebSocket immediately and buffers incoming events into a ref
 *    (`wsBufferRef`) so no live logs are lost during the REST fetch.
 * 2. Concurrently fetches historical logs from `GET /drain3/recent?limit=500`.
 * 3. Atomically merges REST logs ∪ wsBufferRef using a `Map<id, LogEntry>`
 *    for O(1) deduplication via backend-enforced ULIDs, sorts descending by
 *    ULID lexicographic order (newest first), and caps at 500.
 * 4. After the merge, switches to direct state updates for new WebSocket events.
 *
 * ## Deduplication Strategy
 *
 * Every log carries an immutable ULID (`id`) assigned at the point of backend
 * ingestion. Both REST and WebSocket streams emit the identical `id` for the
 * same record, enabling direct `Map.set(log.id, log)` deduplication without
 * composite key hashing or timestamp+service+message string concatenation.
 *
 * Mount this once inside RootLayout so the connection and accumulated data survive
 * navigation between Overview, Live Logs, Anomalies, Analytics, etc.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { LogEntry, LogLevel } from "../types/monitoring";
import type { TelemetryEvent } from "../types/telemetry";
import {
  loadingState,
  staleState,
  successState,
  unavailableState,
  type OperationalDataState,
} from "../types/operational";
import { FEATURE_FLAGS } from "../config/features";
import { mockTelemetry } from "../services/mockTelemetry";
import {
  AuthenticationError,
  authenticatedWebSocketUrl,
  clearAuthToken,
  fetchAuthenticated,
  getAuthToken,
  refreshSession,
} from "../utils/auth";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SOCKET_PATH = "/ws/telemetry";
const MAX_LOGS = 500;
const MAX_RECENT_EVENTS = 30;
const NEW_LOG_HIGHLIGHT_MS = 2000;
const RECONNECT_DELAY_MS = 3000;
/** Throttle interval for flushing the WebSocket log buffer into React state. */
const WS_FLUSH_INTERVAL_MS = 100;
/** REST backfill endpoint. */
const BACKFILL_URL = "/api/v1/logs/recent?limit=500";

// ---------------------------------------------------------------------------
// Telemetry Stream Types (from useTelemetryStream)
// ---------------------------------------------------------------------------

export type BlastRadiusNode = {
  service_name: string;
  impact_classification: "root" | "direct" | "indirect";
  dependency_path: string[];
  propagation_path: string[];
  impact_score: number;
};

export type TrackingLoopEvent = {
  id?: number;
  window_id: string;
  anomaly_score: number;
  severity: string;
  status: string;
  blast_radius?: BlastRadiusNode[] | null;
  suspected_root_service?: string | null;
  root_cause_confidence?: number | null;
  created_at?: string | null;
  history?: Array<{ actor_user_id: number; previous_status: string; new_status: string; note?: string | null; created_at: string }>;
};

export type PerformanceEvent = {
  metric_name: string;
  current_value: number;
  threshold: number;
  severity: string;
  health_metrics?: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// Connection state
// ---------------------------------------------------------------------------

export type ConnectionState = "connecting" | "live" | "reconnecting" | "offline" | "stale" | "auth_required" | "failed";

// ---------------------------------------------------------------------------
// Context shape
// ---------------------------------------------------------------------------

interface TelemetryContextValue {
  // Logs
  logs: LogEntry[];
  newIds: Set<string>;
  totalLogCount: number;

  // Backfill state
  isBackfillLoading: boolean;
  backfillError: string | null;
  logDataState: OperationalDataState<LogEntry[]>;
  trackingLoopsDataState: OperationalDataState<TrackingLoopEvent[]>;
  lastTelemetryAt: string | null;

  // Connection
  connectionState: ConnectionState;
  connectionUrl: string | null;

  // Raw telemetry (for LiveTelemetryStatus)
  latestEvent: TelemetryEvent | null;
  recentEvents: TelemetryEvent[];
  eventCount: number;

  // Anomaly tracking loops
  activeTrackingLoops: TrackingLoopEvent[];
  clearTrackingLoops: () => void;

  // Performance events
  latestPerformanceEvents: PerformanceEvent[];
  clearPerformanceEvents: () => void;

  // Actions
  clearLogs: () => void;
}

const TelemetryContext = createContext<TelemetryContextValue | null>(null);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatEventTimestamp(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  return `${date.toTimeString().slice(0, 8)}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function normalizeLevel(level: unknown): LogLevel | null {
  if (
    level === "INFO" ||
    level === "WARN" ||
    level === "ERROR" ||
    level === "DEBUG" ||
    level === "FATAL" ||
    level === "CRITICAL"
  ) {
    return level;
  }
  return null;
}

function safeTelemetryError(error: unknown): string {
  if (error instanceof AuthenticationError) return "Authentication required";
  if (error instanceof Error && /^HTTP 403\b/.test(error.message)) {
    return "Telemetry is not available for this scope";
  }
  if (error instanceof Error && /^HTTP 401\b/.test(error.message)) {
    return "Authentication required";
  }
  return "Telemetry service unavailable";
}

function buildSocketCandidates(): string[] {
  const wsUrl = import.meta.env.VITE_WS_URL || (window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + '/ws/telemetry';
  return [wsUrl];
}

/**
 * Build the REST backfill URL candidates, mirroring the same host-detection
 * logic used for the WebSocket connection.
 */
function buildBackfillUrls(): string[] {
  const apiUrl = import.meta.env.VITE_API_URL || '';
  return [`${apiUrl.replace(/\/$/, "")}/api/v1/logs/recent?limit=500`];
}

function buildTrackingLoopsBackfillUrls(): string[] {
  const apiUrl = import.meta.env.VITE_API_URL || '';
  return [`${apiUrl.replace(/\/$/, "")}/api/v1/tracking-loops?limit=100`];
}

// NOTE: The legacy `logKey()` composite hashing function has been removed.
// Deduplication is now performed via the backend-enforced ULID `log.id`.

/**
 * Parse a raw JSON string into a TelemetryEvent.
 */
function parseTelemetryEvent(raw: string): TelemetryEvent | null {
  try {
    const parsed = JSON.parse(raw) as Partial<TelemetryEvent>;
    if (
      !parsed ||
      typeof parsed.type !== "string" ||
      typeof parsed.timestamp !== "string" ||
      typeof parsed.payload !== "object"
    ) {
      return null;
    }
    return parsed as TelemetryEvent;
  } catch {
    return null;
  }
}

/**
 * Extract a displayable LogEntry from a `log.parsed` telemetry payload.
 */
function logEntryFromParsedPayload(
  envelope: Record<string, unknown>,
  payload: Record<string, unknown>,
): LogEntry | null {
  const message =
    typeof payload.template === "string" && payload.template
      ? payload.template
      : typeof payload.template_text === "string" && payload.template_text
        ? payload.template_text
        : typeof payload.message === "string" && payload.message
          ? payload.message
          : typeof payload.raw_message === "string" && payload.raw_message
            ? payload.raw_message
            : null;

  if (!message) return null;

  const levelRaw = typeof payload.level === "string" ? payload.level.toUpperCase() : null;
  const level = normalizeLevel(levelRaw);
  const timestamp = formatEventTimestamp(envelope.timestamp);
  const backendId = typeof payload.id === "string" && payload.id ? payload.id : null;
  const service = typeof payload.service === "string" && payload.service ? payload.service : null;
  if (!level || !timestamp || !backendId || !service) return null;

  // Use the backend-assigned ULID directly — it is guaranteed non-null.
  return {
    id: backendId,
    timestamp,
    level,
    service,
    message,
    template_id:
      typeof payload.template_id === "string" ? payload.template_id : undefined,
    metadata:
      typeof payload.metadata === "object" && payload.metadata !== null
        ? (payload.metadata as Record<string, unknown>)
        : undefined,
    latency_ms:
      typeof payload.latency_ms === "number" && Number.isFinite(payload.latency_ms)
        ? payload.latency_ms
        : undefined,
  };
}

/**
 * Convert a backend ParsedLog dict (from /drain3/recent) into a LogEntry.
 */
function logEntryFromBackendRecord(record: Record<string, unknown>): LogEntry | null {
  const raw = record.raw_message ?? record.message;
  const template = record.template_text ?? record.template;
  const message =
    typeof template === "string" && template
      ? template
      : typeof raw === "string" && raw
        ? raw
        : null;

  if (!message) return null;

  const levelRaw = typeof record.level === "string" ? record.level.toUpperCase() : null;
  const level = normalizeLevel(levelRaw);

  const timestampStr = formatEventTimestamp(record.timestamp);
  const service = typeof record.service === "string" && record.service ? record.service : null;
  const backendId = typeof record.id === "string" && record.id ? record.id : null;
  if (!level || !timestampStr || !service || !backendId) return null;

  return {
    id: backendId,
    timestamp: timestampStr,
    level,
    service,
    message,
    template_id:
      typeof record.template_id === "string" ? record.template_id : undefined,
    metadata:
      typeof record.metadata === "object" && record.metadata !== null
        ? (record.metadata as Record<string, unknown>)
        : undefined,
    latency_ms:
      typeof record.latency_ms === "number" && Number.isFinite(record.latency_ms)
        ? record.latency_ms
        : undefined,
  };
}

/**
 * Deduplicate and merge log arrays using O(1) Map lookups on backend ULID `id`.
 * Returns logs sorted descending by ULID (newest first), capped to MAX_LOGS.
 *
 * ULIDs are 128-bit time-ordered identifiers — their lexicographic string
 * comparison is equivalent to chronological ordering, making `localeCompare`
 * a correct and faster substitute for `new Date()` parsing.
 */
export function deduplicateAndMerge(
  ...sources: LogEntry[][]
): LogEntry[] {
  const map = new Map<string, LogEntry>();

  for (const source of sources) {
    for (const log of source) {
      // Direct O(1) identity dedup via backend-enforced ULID
      if (!map.has(log.id)) {
        map.set(log.id, log);
      }
    }
  }

  return Array.from(map.values())
    .sort((a, b) => b.id.localeCompare(a.id))
    .slice(0, MAX_LOGS);
}

// ---------------------------------------------------------------------------
// Type guards
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isBlastRadiusNode(value: unknown): value is BlastRadiusNode {
  if (!isRecord(value)) return false;
  return (
    typeof value.service_name === "string" &&
    (value.impact_classification === "root" ||
      value.impact_classification === "direct" ||
      value.impact_classification === "indirect") &&
    isStringArray(value.dependency_path) &&
    isStringArray(value.propagation_path) &&
    typeof value.impact_score === "number"
  );
}

function isBlastRadius(value: unknown): value is BlastRadiusNode[] {
  return Array.isArray(value) && value.every(isBlastRadiusNode);
}

function isTrackingLoopEvent(value: unknown): value is TrackingLoopEvent {
  if (!isRecord(value)) return false;
  return (
    typeof value.window_id === "string" &&
    value.window_id.length > 0 &&
    typeof value.anomaly_score === "number" &&
    Number.isFinite(value.anomaly_score) &&
    typeof value.severity === "string" &&
    value.severity.length > 0 &&
    typeof value.status === "string" &&
    value.status.length > 0 &&
    (value.created_at === undefined ||
      value.created_at === null ||
      (typeof value.created_at === "string" && Number.isFinite(new Date(value.created_at).getTime()))) &&
    (value.blast_radius === undefined ||
      value.blast_radius === null ||
      isBlastRadius(value.blast_radius)) &&
    (value.suspected_root_service === undefined ||
      value.suspected_root_service === null ||
      typeof value.suspected_root_service === "string")
  );
}

function trackingLoopFromAnomaly(value: Record<string, unknown>): TrackingLoopEvent | null {
  if (
    typeof value.window_id !== "string" ||
    !value.window_id ||
    typeof value.anomaly_score !== "number" ||
    !Number.isFinite(value.anomaly_score) ||
    typeof value.severity !== "string" ||
    !value.severity
  ) {
    return null;
  }

  return {
    window_id: value.window_id,
    anomaly_score: value.anomaly_score,
    severity: value.severity,
    status: "triggered",
    suspected_root_service:
      typeof value.service === "string"
        ? value.service
        : typeof value.suspected_root_service === "string"
          ? value.suspected_root_service
          : null,
    blast_radius: isBlastRadius(value.blast_radius) ? value.blast_radius : null,
  };
}

function isPerformanceEvent(value: unknown): value is PerformanceEvent {
  if (!isRecord(value)) return false;
  return (
    typeof value.metric_name === "string" &&
    typeof value.current_value === "number" &&
    typeof value.threshold === "number" &&
    typeof value.severity === "string" &&
    (value.health_metrics === undefined ||
      value.health_metrics === null ||
      isRecord(value.health_metrics))
  );
}

// ---------------------------------------------------------------------------
// REST backfill fetcher
// ---------------------------------------------------------------------------

async function fetchBackfillLogs(): Promise<LogEntry[]> {
  if (FEATURE_FLAGS.ENABLE_DEMO_MODE) {
    return mockTelemetry.getInitialBackfillLogs(40);
  }

  const urls = buildBackfillUrls();
  let lastError: Error | null = null;

  for (const url of urls) {
    try {
      const response = await fetchAuthenticated(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data = (await response.json()) as Record<string, unknown>;
      const rawLogs = data.logs;
      if (!Array.isArray(rawLogs)) {
        throw new Error("Telemetry response schema invalid");
      }

      const entries: LogEntry[] = [];
      for (const raw of rawLogs) {
        if (isRecord(raw)) {
          const entry = logEntryFromBackendRecord(raw);
          if (entry) entries.push(entry);
        }
      }
      return entries;
    } catch (err) {
      if (err instanceof AuthenticationError) throw err;
      lastError = err instanceof Error ? err : new Error(String(err));
    }
  }

  throw lastError ?? new Error("All backfill URLs failed");
}

async function fetchBackfillTrackingLoops(): Promise<TrackingLoopEvent[]> {
  if (FEATURE_FLAGS.ENABLE_DEMO_MODE) {
    return [];
  }

  const urls = buildTrackingLoopsBackfillUrls();
  let lastError: Error | null = null;

  for (const url of urls) {
    try {
      const response = await fetchAuthenticated(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data = await response.json();
      if (!Array.isArray(data)) {
        throw new Error("Tracking-loop response schema invalid");
      }

      const entries: TrackingLoopEvent[] = [];
      for (const raw of data) {
        if (isTrackingLoopEvent(raw)) {
          entries.push(raw);
        }
      }
      return entries;
    } catch (err) {
      if (err instanceof AuthenticationError) throw err;
      lastError = err instanceof Error ? err : new Error(String(err));
    }
  }

  throw lastError ?? new Error("All tracking loops backfill URLs failed");
}

// ---------------------------------------------------------------------------
// Provider Component
// ---------------------------------------------------------------------------

export function TelemetryProvider({ children }: { children: ReactNode }) {
  // ---- Global logs state ----
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [totalLogCount, setTotalLogCount] = useState(0);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const highlightedIdsRef = useRef<Set<string>>(new Set());
  const cleanupTimersRef = useRef<number[]>([]);

  // ---- Backfill state ----
  const [isBackfillLoading, setIsBackfillLoading] = useState(true);
  const [backfillError, setBackfillError] = useState<string | null>(null);
  const [logDataState, setLogDataState] = useState<OperationalDataState<LogEntry[]>>(
    loadingState<LogEntry[]>("REST logs + telemetry stream"),
  );
  const [trackingLoopsDataState, setTrackingLoopsDataState] = useState<
    OperationalDataState<TrackingLoopEvent[]>
  >(loadingState<TrackingLoopEvent[]>("REST tracking-loop history + telemetry stream"));
  const [lastTelemetryAt, setLastTelemetryAt] = useState<string | null>(null);
  const backfillCompleteRef = useRef(false);

  /**
   * WebSocket log buffer — accumulates LogEntry objects received via WebSocket
   * while the REST backfill is still in-flight. After merge, this buffer is
   * no longer used; logs go directly into React state via the flush interval.
   */
  const wsBufferRef = useRef<LogEntry[]>([]);

  // ---- Connection state ----
  const [connectionState, setConnectionState] = useState<ConnectionState>(navigator.onLine ? "connecting" : "offline");
  const [connectionUrl, setConnectionUrl] = useState<string | null>(null);

  // ---- Raw telemetry events (for LiveTelemetryStatus) ----
  const [latestEvent, setLatestEvent] = useState<TelemetryEvent | null>(null);
  const [recentEvents, setRecentEvents] = useState<TelemetryEvent[]>([]);
  const [eventCount, setEventCount] = useState(0);

  // ---- Tracking loops & performance events ----
  const [activeTrackingLoops, setActiveTrackingLoops] = useState<
    Record<string, TrackingLoopEvent>
  >({});
  const [latestPerformanceEvents, setLatestPerformanceEvents] = useState<
    Record<string, PerformanceEvent>
  >({});
  const pendingTrackingUpdates = useRef<TrackingLoopEvent[]>([]);
  const pendingPerformanceUpdates = useRef<PerformanceEvent[]>([]);

  // ---- Socket refs ----
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const stableConnectionTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef<number>(0);
  const connectionStateRef = useRef<ConnectionState>(navigator.onLine ? "connecting" : "offline");
  const activeCandidateRef = useRef(0);

  const socketCandidates = useMemo(buildSocketCandidates, []);

  // ---- Batched tracking/perf flush (2 Hz) ----
  useEffect(() => {
    const timer = setInterval(() => {
      if (pendingTrackingUpdates.current.length > 0) {
        const updates = pendingTrackingUpdates.current;
        setActiveTrackingLoops((prev) => {
          const next = { ...prev };
          updates.forEach((e) => {
            next[e.window_id] = e;
          });
          return next;
        });
        setTrackingLoopsDataState((previous) => {
          const current = previous.data ?? [];
          const byWindow = new Map(current.map((loop) => [loop.window_id, loop]));
          updates.forEach((loop) => byWindow.set(loop.window_id, loop));
          return successState(
            Array.from(byWindow.values()),
            new Date().toISOString(),
            "WebSocket telemetry",
            byWindow.size === 0,
          );
        });
        setLastTelemetryAt(new Date().toISOString());
        pendingTrackingUpdates.current = [];
      }

      if (pendingPerformanceUpdates.current.length > 0) {
        setLatestPerformanceEvents((prev) => {
          const next = { ...prev };
          pendingPerformanceUpdates.current.forEach((e) => {
            next[e.metric_name] = e;
          });
          return next;
        });
        pendingPerformanceUpdates.current = [];
      }
    }, 500);

    return () => clearInterval(timer);
  }, []);

  // ---- Throttled WebSocket log flush (post-backfill) ----
  // After backfill completes, incoming WS logs accumulate in wsBufferRef
  // and are flushed into React state every WS_FLUSH_INTERVAL_MS to avoid
  // per-message re-renders during high-throughput bursts.
  useEffect(() => {
    const timer = setInterval(() => {
      if (!backfillCompleteRef.current) return;
      if (wsBufferRef.current.length === 0) return;

      const batch = wsBufferRef.current;
      wsBufferRef.current = [];
      setTotalLogCount(prev => prev + batch.length);

      setLogs((prev) => {
        const merged = deduplicateAndMerge(prev, batch);
        return merged;
      });
      const telemetryUpdatedAt = new Date().toISOString();
      setLogDataState((previous) => {
        const merged = deduplicateAndMerge(previous.data ?? [], batch);
        return successState(merged, telemetryUpdatedAt, "WebSocket telemetry", false);
      });
      setLastTelemetryAt(telemetryUpdatedAt);

      // Highlight new entries
      for (const entry of batch) {
        if (highlightedIdsRef.current.has(entry.id)) continue;
        highlightedIdsRef.current.add(entry.id);

        setNewIds((prev) => {
          const next = new Set(prev);
          next.add(entry.id);
          return next;
        });

        const timerId = window.setTimeout(() => {
          setNewIds((prev) => {
            const next = new Set(prev);
            next.delete(entry.id);
            return next;
          });
          highlightedIdsRef.current.delete(entry.id);
        }, NEW_LOG_HIGHLIGHT_MS);

        cleanupTimersRef.current.push(timerId);
      }
    }, WS_FLUSH_INTERVAL_MS);

    return () => clearInterval(timer);
  }, []);

  // ---- Extract LogEntry objects from WebSocket telemetry events ----
  const extractLogEntries = useCallback(
    (event: TelemetryEvent): LogEntry[] => {
      const entries: LogEntry[] = [];
      const eventType: string = event.type;

      if (eventType === "frame_update" && isRecord(event.payload)) {
        const framePayload = event.payload as Record<string, unknown>;
        if (Array.isArray(framePayload.events)) {
          for (const innerEvent of framePayload.events) {
            if (!isRecord(innerEvent)) continue;
            if (innerEvent.type === "log.parsed" && isRecord(innerEvent.payload)) {
              const entry = logEntryFromParsedPayload(
                innerEvent as Record<string, unknown>,
                innerEvent.payload,
              );
              if (entry) entries.push(entry);
            }
          }
        }
      } else if (eventType === "log.parsed" && isRecord(event.payload)) {
        const entry = logEntryFromParsedPayload(
          event as unknown as Record<string, unknown>,
          event.payload as Record<string, unknown>,
        );
        if (entry) entries.push(entry);
      }

      return entries;
    },
    [],
  );

  // ---- Process a single telemetry event for all subsystems ----
  const processEvent = useCallback(
    (event: TelemetryEvent) => {
      // 1. Store as raw event (for LiveTelemetryStatus)
      setLatestEvent(event);
      setEventCount((c) => c + 1);
      setRecentEvents((events) => [event, ...events].slice(0, MAX_RECENT_EVENTS));

      // 2. Extract log entries and push to buffer
      const logEntries = extractLogEntries(event);
      if (logEntries.length > 0) {
        wsBufferRef.current.push(...logEntries);
      }

      const eventType: string = event.type;

      // 3. Handle tracking loops, anomalies, and performance events
      if (eventType === "frame_update" && isRecord(event.payload)) {
        const framePayload = event.payload as Record<string, unknown>;
        if (Array.isArray(framePayload.events)) {
          for (const innerEvent of framePayload.events) {
            if (!isRecord(innerEvent)) continue;
            const innerType = innerEvent.type;
            const innerPayload = innerEvent.payload;

            if (
              innerType === "infrastructure.tracking_loop.triggered" &&
              isTrackingLoopEvent(innerPayload)
            ) {
              pendingTrackingUpdates.current.push({
                ...innerPayload,
                status: "triggered",
              });
            } else if (innerType === "anomaly.detected" && isRecord(innerPayload)) {
              const anomaly = trackingLoopFromAnomaly(innerPayload);
              if (anomaly) pendingTrackingUpdates.current.push(anomaly);
            } else if (
              innerType === "infrastructure.performance.alert" &&
              isPerformanceEvent(innerPayload)
            ) {
              pendingPerformanceUpdates.current.push(innerPayload);
            }
          }
        }
      } else if (
        eventType === "infrastructure.tracking_loop.triggered" &&
        isTrackingLoopEvent(event.payload)
      ) {
        pendingTrackingUpdates.current.push({
          ...event.payload,
          status: "triggered",
        });
      } else if (eventType === "anomaly.detected" && isRecord(event.payload)) {
        const anomaly = trackingLoopFromAnomaly(event.payload);
        if (anomaly) pendingTrackingUpdates.current.push(anomaly);
      } else if (
        eventType === "infrastructure.performance.alert" &&
        isPerformanceEvent(event.payload)
      ) {
        pendingPerformanceUpdates.current.push(event.payload as PerformanceEvent);
      }
    },
    [extractLogEntries],
  );

  // ---- REST backfill (runs once on mount) ----
  useEffect(() => {
    let cancelled = false;

    Promise.allSettled([fetchBackfillLogs(), fetchBackfillTrackingLoops()])
      .then((results) => {
        if (cancelled) return;

        const [logsResult, trackingResult] = results;
        const refreshedAt = new Date().toISOString();
        const bufferedWsLogs = [...wsBufferRef.current];
        wsBufferRef.current = [];

        if (logsResult.status === "fulfilled") {
          const merged = deduplicateAndMerge(logsResult.value, bufferedWsLogs);
          setLogs(merged);
          setTotalLogCount((previous) => previous + merged.length);
          setLogDataState(
            successState(
              merged,
              refreshedAt,
              bufferedWsLogs.length > 0 ? "REST + WebSocket telemetry" : "REST logs",
              merged.length === 0,
            ),
          );
          if (merged.length > 0) setLastTelemetryAt(refreshedAt);
        } else if (bufferedWsLogs.length > 0) {
          setLogs(deduplicateAndMerge(bufferedWsLogs));
          setTotalLogCount((previous) => previous + bufferedWsLogs.length);
          setLogDataState(successState(bufferedWsLogs, refreshedAt, "WebSocket telemetry", false));
          setLastTelemetryAt(refreshedAt);
        } else {
          setLogDataState(unavailableState("REST logs", safeTelemetryError(logsResult.reason)));
        }

        if (trackingResult.status === "fulfilled") {
          const loops = trackingResult.value;
          setActiveTrackingLoops((previous) => {
            const next = { ...previous };
            loops.forEach((loop) => {
              next[loop.window_id] = loop;
            });
            return next;
          });
          setTrackingLoopsDataState(
            successState(loops, refreshedAt, "REST tracking-loop history", loops.length === 0),
          );
        } else {
          setTrackingLoopsDataState(
            unavailableState("REST tracking-loop history", safeTelemetryError(trackingResult.reason)),
          );
        }

        const failures = [logsResult, trackingResult].filter(
          (result): result is PromiseRejectedResult => result.status === "rejected",
        );
        setBackfillError(failures.length > 0 ? safeTelemetryError(failures[0].reason) : null);
        setIsBackfillLoading(false);
        backfillCompleteRef.current = true;

        // Atomic merge: REST logs ∪ wsBufferRef
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // ---- Persistent WebSocket connection / Demo Mode Mock ----
  useEffect(() => {
    let cancelled = false;
    let reconnectEnabled = true;

    function updateConnectionState(nextState: ConnectionState) {
      connectionStateRef.current = nextState;
      setConnectionState(nextState);
    }

    if (FEATURE_FLAGS.ENABLE_DEMO_MODE) {
      updateConnectionState("live");
      setConnectionUrl("mock://in-browser-telemetry-emitter");
      mockTelemetry.start();
      const unsubscribe = mockTelemetry.subscribe((event) => {
        if (!cancelled) {
          processEvent(event);
        }
      });

      return () => {
        cancelled = true;
        unsubscribe();
        mockTelemetry.stop();
      };
    }

    const clearReconnectTimer = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const clearStableConnectionTimer = () => {
      if (stableConnectionTimerRef.current !== null) {
        window.clearTimeout(stableConnectionTimerRef.current);
        stableConnectionTimerRef.current = null;
      }
    };

    const scheduleReconnect = (nextIndex: number) => {
      clearReconnectTimer();

      if (!navigator.onLine) { updateConnectionState("offline"); return; }

      const attempt = reconnectAttemptsRef.current;
      if (attempt >= 8) { updateConnectionState("failed"); return; }
      const backoffMs = Math.min(30000, RECONNECT_DELAY_MS * Math.pow(1.5, attempt));
      const jitter = Math.random() * Math.min(1000, backoffMs * 0.25);
      const delay = backoffMs + jitter;

      reconnectAttemptsRef.current += 1;

      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        connect(nextIndex);
      }, delay);
    };

    const connect = (index: number) => {
      if (cancelled) return;

      const candidate = socketCandidates[index];
      if (!candidate) {
        updateConnectionState("failed");
        setConnectionUrl(null);
        return;
      }

      clearReconnectTimer();
      updateConnectionState(reconnectAttemptsRef.current > 0 ? "reconnecting" : "connecting");
      setConnectionUrl(candidate);
      activeCandidateRef.current = index;

      // Resolve the current token at every connect/reconnect attempt. The
      // tokenized URL is used only by the WebSocket constructor; UI state keeps
      // the token-free candidate so it cannot be rendered or logged accidentally.
      const authenticatedCandidate = authenticatedWebSocketUrl(candidate);
      if (!authenticatedCandidate) {
        updateConnectionState("auth_required");
        return;
      }

      const socket = new WebSocket(authenticatedCandidate);
      socketRef.current = socket;

      socket.onopen = () => {
        if (cancelled || socketRef.current !== socket) return;
        updateConnectionState("live");
        setConnectionUrl(candidate);
        clearStableConnectionTimer();
        stableConnectionTimerRef.current = window.setTimeout(() => {
          stableConnectionTimerRef.current = null;
          if (socketRef.current === socket && socket.readyState === WebSocket.OPEN) reconnectAttemptsRef.current = 0;
        }, 10000);
        // WebSocket is notification transport. Reconcile durable history after every reconnect.
        void Promise.allSettled([fetchBackfillLogs(), fetchBackfillTrackingLoops()]).then((results) => {
          if (cancelled || socketRef.current !== socket) return;
          const [logsResult, trackingResult] = results;
          const reconciledAt = new Date().toISOString();

          if (logsResult.status === "fulfilled") {
            setLogs((current) => deduplicateAndMerge(current, logsResult.value));
            setLogDataState((previous) => {
              const merged = deduplicateAndMerge(previous.data ?? [], logsResult.value);
              return successState(merged, reconciledAt, "REST reconciliation", merged.length === 0);
            });
            if (logsResult.value.length > 0) setLastTelemetryAt(reconciledAt);
          } else {
            setLogDataState((previous) =>
              previous.data
                ? staleState(previous.data, previous.lastUpdated, previous.source, safeTelemetryError(logsResult.reason))
                : unavailableState("REST reconciliation", safeTelemetryError(logsResult.reason)),
            );
          }

          if (trackingResult.status === "fulfilled") {
            setActiveTrackingLoops((current) => {
              const next = { ...current };
              trackingResult.value.forEach((incident) => { next[incident.window_id] = incident; });
              return next;
            });
            setTrackingLoopsDataState(
              successState(
                trackingResult.value,
                reconciledAt,
                "REST reconciliation",
                trackingResult.value.length === 0,
              ),
            );
          } else {
            setTrackingLoopsDataState((previous) =>
              previous.data
                ? staleState(previous.data, previous.lastUpdated, previous.source, safeTelemetryError(trackingResult.reason))
                : unavailableState("REST reconciliation", safeTelemetryError(trackingResult.reason)),
            );
          }

          const failure = [logsResult, trackingResult].find(
            (result): result is PromiseRejectedResult => result.status === "rejected",
          );
          setBackfillError(failure ? safeTelemetryError(failure.reason) : null);
        });
        
        // Send the authentication handshake
        const token = getAuthToken();
        if (token) {
          socket.send(JSON.stringify({ type: "auth", token }));
        } else {
          // If no token is available, clear credential and trigger disconnect flow
          clearAuthToken();
          reconnectEnabled = false;
          updateConnectionState("auth_required");
          socket.close();
        }
      };

      socket.onmessage = (msg) => {
        if (cancelled || socketRef.current !== socket) return;
        if (typeof msg.data !== "string") return;

        const event = parseTelemetryEvent(msg.data);
        if (event) {
          processEvent(event);
        }
      };

      socket.onerror = () => {
        if (cancelled || socketRef.current !== socket) return;
        if (connectionStateRef.current === "connecting") {
          connect(index + 1);
          return;
        }
        updateConnectionState("stale");
      };

      socket.onclose = (closeEvent) => {
        if (cancelled || socketRef.current !== socket) return;
        socketRef.current = null;

        if (!reconnectEnabled) return;

        // The backend uses 1008 for an invalid/expired JWT. Clear the stale
        // credential and stop the reconnect loop; REST backfill follows the
        // same clear-and-surface-auth-error contract.
        if (closeEvent.code === 1008) {
          updateConnectionState("auth_required");
          void refreshSession().then((token) => {
            if (cancelled) return;
            if (token) scheduleReconnect(0);
            else { clearAuthToken(); reconnectEnabled = false; updateConnectionState("auth_required"); }
          });
          return;
        }

        if (connectionStateRef.current === "live") {
          updateConnectionState("stale");
          scheduleReconnect(activeCandidateRef.current);
          return;
        }
        scheduleReconnect(index + 1 < socketCandidates.length ? index + 1 : 0);
      };
    };

    const handleOffline = () => {
      clearReconnectTimer();
      clearStableConnectionTimer();
      updateConnectionState("offline");
      socketRef.current?.close();
    };
    const handleOnline = () => {
      if (cancelled || !reconnectEnabled) return;
      reconnectAttemptsRef.current = 0;
      scheduleReconnect(0);
    };
    window.addEventListener("offline", handleOffline);
    window.addEventListener("online", handleOnline);

    connect(0);

    return () => {
      cancelled = true;
      reconnectEnabled = false;
      window.removeEventListener("offline", handleOffline);
      window.removeEventListener("online", handleOnline);
      clearReconnectTimer();
      clearStableConnectionTimer();

      const socket = socketRef.current;
      socketRef.current = null;
      if (socket && socket.readyState <= WebSocket.OPEN) {
        socket.close();
      }
    };
  }, [socketCandidates, processEvent]);

  // ---- Cleanup highlight timers on unmount ----
  useEffect(() => {
    return () => {
      for (const timerId of cleanupTimersRef.current) {
        window.clearTimeout(timerId);
      }
      cleanupTimersRef.current = [];
    };
  }, []);

  // ---- Public actions ----
  const clearTrackingLoops = useCallback(() => setActiveTrackingLoops({}), []);
  const clearPerformanceEvents = useCallback(() => setLatestPerformanceEvents({}), []);
  const clearLogs = useCallback(() => {
    setLogs([]);
    setTotalLogCount(0);
    setNewIds(new Set());
    highlightedIdsRef.current.clear();
  }, []);

  const value: TelemetryContextValue = useMemo(
    () => ({
      logs,
      newIds,
      totalLogCount,
      isBackfillLoading,
      backfillError,
      logDataState,
      trackingLoopsDataState,
      lastTelemetryAt,
      connectionState,
      connectionUrl,
      latestEvent,
      recentEvents,
      eventCount,
      activeTrackingLoops: Object.values(activeTrackingLoops),
      clearTrackingLoops,
      latestPerformanceEvents: Object.values(latestPerformanceEvents),
      clearPerformanceEvents,
      clearLogs,
    }),
    [
      logs,
      newIds,
      totalLogCount,
      isBackfillLoading,
      backfillError,
      logDataState,
      trackingLoopsDataState,
      lastTelemetryAt,
      connectionState,
      connectionUrl,
      latestEvent,
      recentEvents,
      eventCount,
      activeTrackingLoops,
      clearTrackingLoops,
      latestPerformanceEvents,
      clearPerformanceEvents,
      clearLogs,
    ],
  );

  return (
    <TelemetryContext.Provider value={value}>
      {/* WebSocket disconnection / error banner */}
      {connectionState !== "live" && connectionState !== "connecting" && (
        <div
          role="alert"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            zIndex: 9999,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            padding: "8px 16px",
            fontSize: 12,
            fontWeight: 600,
            fontFamily:
              'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
            color: connectionState === "failed" || connectionState === "auth_required" ? "#f85149" : "#d29922",
            background:
              connectionState === "failed" || connectionState === "auth_required"
                ? "rgba(248,81,73,0.12)"
                : "rgba(210,153,34,0.12)",
            borderBottom:
              connectionState === "failed" || connectionState === "auth_required"
                ? "1px solid rgba(248,81,73,0.25)"
                : "1px solid rgba(210,153,34,0.25)",
          }}
        >
          <span style={{ fontSize: 14 }}>
            {connectionState === "failed" || connectionState === "auth_required" ? "⚠" : "⟳"}
          </span>
          <span>
            {connectionState === "offline" ? "Browser offline — telemetry is stale" : connectionState === "auth_required" ? "Session expired — sign in is required" : connectionState === "failed" ? "Telemetry reconnect limit reached — reload to retry" : connectionState === "stale" ? "Telemetry disconnected — displayed data is stale" : "Reconnecting to telemetry stream…"}
          </span>
        </div>
      )}
      {children}
    </TelemetryContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook for consuming the context
// ---------------------------------------------------------------------------

export function useTelemetryContext(): TelemetryContextValue {
  const ctx = useContext(TelemetryContext);
  if (!ctx) {
    throw new Error("useTelemetryContext must be used within a <TelemetryProvider>");
  }
  return ctx;
}

/**
 * TelemetryProvider — Global telemetry context that maintains a single persistent
 * WebSocket connection and shared state (logs, tracking loops, performance events,
 * raw telemetry events) across all dashboard route transitions.
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

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SOCKET_PATH = "/ws/telemetry";
const BACKUP_SOCKET_URL = "ws://localhost:8000/ws/telemetry";
const MAX_LOGS = 500;
const MAX_RECENT_EVENTS = 30;
const NEW_LOG_HIGHLIGHT_MS = 2000;
const RECONNECT_DELAY_MS = 3000;

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
  window_id: string;
  anomaly_score: number;
  severity: string;
  status: string;
  blast_radius?: BlastRadiusNode[] | null;
  suspected_root_service?: string | null;
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

export type ConnectionState = "connecting" | "connected" | "disconnected" | "error";

// ---------------------------------------------------------------------------
// Context shape
// ---------------------------------------------------------------------------

interface TelemetryContextValue {
  // Logs
  logs: LogEntry[];
  newIds: Set<string>;

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
}

const TelemetryContext = createContext<TelemetryContextValue | null>(null);

// ---------------------------------------------------------------------------
// Helpers (ported from useLiveLogs.ts)
// ---------------------------------------------------------------------------

function getTimestamp() {
  return `${new Date().toTimeString().slice(0, 8)}.${String(Date.now() % 1000).padStart(3, "0")}`;
}

function normalizeLevel(level: unknown): LogLevel {
  if (level === "INFO" || level === "WARN" || level === "ERROR" || level === "DEBUG") {
    return level;
  }
  return "INFO";
}

function buildSocketCandidates(): string[] {
  const candidates = [
    import.meta.env.VITE_WS_URL?.trim(),
    `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${SOCKET_PATH}`,
    BACKUP_SOCKET_URL,
  ];
  return candidates.filter((c): c is string => Boolean(c));
}

/**
 * Parse a raw JSON string into a TelemetryEvent (same logic as useTelemetrySocket).
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
 * Extract displayable LogEntry objects from a `log.parsed` telemetry payload.
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

  const levelRaw = typeof payload.level === "string" ? payload.level.toUpperCase() : "INFO";

  return {
    id: `ws-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    timestamp:
      typeof envelope.timestamp === "string"
        ? new Date(envelope.timestamp).toTimeString().slice(0, 8) +
          "." +
          String(new Date(envelope.timestamp as string).getMilliseconds()).padStart(3, "0")
        : getTimestamp(),
    level: normalizeLevel(levelRaw),
    service:
      typeof payload.service === "string" && payload.service ? payload.service : "backend",
    message,
  };
}

// ---------------------------------------------------------------------------
// Type guards (from useTelemetryStream.ts)
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
    typeof value.anomaly_score === "number" &&
    typeof value.severity === "string" &&
    typeof value.status === "string" &&
    (value.blast_radius === undefined ||
      value.blast_radius === null ||
      isBlastRadius(value.blast_radius)) &&
    (value.suspected_root_service === undefined ||
      value.suspected_root_service === null ||
      typeof value.suspected_root_service === "string")
  );
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
// Provider Component
// ---------------------------------------------------------------------------

export function TelemetryProvider({ children }: { children: ReactNode }) {
  // ---- Global logs state ----
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const highlightedIdsRef = useRef<Set<string>>(new Set());
  const cleanupTimersRef = useRef<number[]>([]);

  // ---- Connection state ----
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
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
  const connectionStateRef = useRef<ConnectionState>("connecting");
  const activeCandidateRef = useRef(0);

  const socketCandidates = useMemo(buildSocketCandidates, []);

  // ---- Batched tracking/perf flush (2 Hz) ----
  useEffect(() => {
    const timer = setInterval(() => {
      if (pendingTrackingUpdates.current.length > 0) {
        setActiveTrackingLoops((prev) => {
          const next = { ...prev };
          pendingTrackingUpdates.current.forEach((e) => {
            next[e.window_id] = e;
          });
          return next;
        });
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

  // ---- Mark new log entries with temporary highlight ----
  const markNewEntries = useCallback((entries: LogEntry[]) => {
    if (!entries.length) return;

    setLogs((previousLogs) => {
      const nextLogs = [...previousLogs];

      for (const entry of entries) {
        if (highlightedIdsRef.current.has(entry.id)) continue;

        highlightedIdsRef.current.add(entry.id);
        nextLogs.push(entry);

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

      return nextLogs.slice(-MAX_LOGS);
    });
  }, []);

  // ---- Process a single telemetry event for all subsystems ----
  const processEvent = useCallback(
    (event: TelemetryEvent) => {
      // 1. Store as raw event (for LiveTelemetryStatus)
      setLatestEvent(event);
      setEventCount((c) => c + 1);
      setRecentEvents((events) => [event, ...events].slice(0, MAX_RECENT_EVENTS));

      const eventType: string = event.type;

      // 2. Handle frame_update envelopes (batched events from backend)
      if (eventType === "frame_update" && isRecord(event.payload)) {
        const framePayload = event.payload as Record<string, unknown>;
        if (Array.isArray(framePayload.events)) {
          const logEntries: LogEntry[] = [];

          for (const innerEvent of framePayload.events) {
            if (!isRecord(innerEvent)) continue;
            const innerType = innerEvent.type;
            const innerPayload = innerEvent.payload;

            // log.parsed → extract log entries
            if (innerType === "log.parsed" && isRecord(innerPayload)) {
              const entry = logEntryFromParsedPayload(
                innerEvent as Record<string, unknown>,
                innerPayload,
              );
              if (entry) logEntries.push(entry);
            }

            // tracking loop → queue for batched update
            if (
              innerType === "infrastructure.tracking_loop.triggered" &&
              isTrackingLoopEvent(innerPayload)
            ) {
              pendingTrackingUpdates.current.push(innerPayload);
            }

            // performance alert → queue for batched update
            if (
              innerType === "infrastructure.performance.alert" &&
              isPerformanceEvent(innerPayload)
            ) {
              pendingPerformanceUpdates.current.push(innerPayload);
            }
          }

          if (logEntries.length > 0) {
            markNewEntries(logEntries);
          }
        }
        return;
      }

      // 3. Direct (non-batched) tracking loop event
      if (
        eventType === "infrastructure.tracking_loop.triggered" &&
        isTrackingLoopEvent(event.payload)
      ) {
        pendingTrackingUpdates.current.push(event.payload as TrackingLoopEvent);
        return;
      }

      // 4. Direct performance alert
      if (
        eventType === "infrastructure.performance.alert" &&
        isPerformanceEvent(event.payload)
      ) {
        pendingPerformanceUpdates.current.push(event.payload as PerformanceEvent);
        return;
      }

      // 5. Direct log.parsed event (non-batched)
      if (eventType === "log.parsed" && isRecord(event.payload)) {
        const entry = logEntryFromParsedPayload(
          event as unknown as Record<string, unknown>,
          event.payload as Record<string, unknown>,
        );
        if (entry) {
          markNewEntries([entry]);
        }
      }
    },
    [markNewEntries],
  );

  // ---- Persistent WebSocket connection ----
  useEffect(() => {
    let cancelled = false;
    let reconnectEnabled = true;

    const clearReconnectTimer = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const updateConnectionState = (nextState: ConnectionState) => {
      connectionStateRef.current = nextState;
      setConnectionState(nextState);
    };

    const scheduleReconnect = (nextIndex: number) => {
      clearReconnectTimer();
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        connect(nextIndex);
      }, RECONNECT_DELAY_MS);
    };

    const connect = (index: number) => {
      if (cancelled) return;

      const candidate = socketCandidates[index];
      if (!candidate) {
        updateConnectionState("error");
        setConnectionUrl(null);
        return;
      }

      clearReconnectTimer();
      updateConnectionState("connecting");
      setConnectionUrl(candidate);
      activeCandidateRef.current = index;

      const socket = new WebSocket(candidate);
      socketRef.current = socket;

      socket.onopen = () => {
        if (cancelled || socketRef.current !== socket) return;
        updateConnectionState("connected");
        setConnectionUrl(candidate);
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
        updateConnectionState("error");
      };

      socket.onclose = () => {
        if (cancelled || socketRef.current !== socket) return;
        socketRef.current = null;

        if (!reconnectEnabled) return;

        if (connectionStateRef.current === "connected") {
          updateConnectionState("disconnected");
          scheduleReconnect(activeCandidateRef.current);
          return;
        }

        connect(index + 1);
      };
    };

    connect(0);

    return () => {
      cancelled = true;
      reconnectEnabled = false;
      clearReconnectTimer();

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

  const value: TelemetryContextValue = useMemo(
    () => ({
      logs,
      newIds,
      connectionState,
      connectionUrl,
      latestEvent,
      recentEvents,
      eventCount,
      activeTrackingLoops: Object.values(activeTrackingLoops),
      clearTrackingLoops,
      latestPerformanceEvents: Object.values(latestPerformanceEvents),
      clearPerformanceEvents,
    }),
    [
      logs,
      newIds,
      connectionState,
      connectionUrl,
      latestEvent,
      recentEvents,
      eventCount,
      activeTrackingLoops,
      clearTrackingLoops,
      latestPerformanceEvents,
      clearPerformanceEvents,
    ],
  );

  return <TelemetryContext.Provider value={value}>{children}</TelemetryContext.Provider>;
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

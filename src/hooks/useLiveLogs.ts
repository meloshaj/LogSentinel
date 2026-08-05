import { useEffect, useMemo, useRef, useState } from "react";
import type { LogEntry, LogLevel } from "../types/monitoring";

type ConnectionState = "connecting" | "connected" | "disconnected" | "error";

const SOCKET_PATH = "/ws/telemetry";
const BACKUP_SOCKET_URL = "ws://localhost:8000/ws/telemetry";
const MAX_VISIBLE_LOGS = 2000;
const NEW_LOG_HIGHLIGHT_MS = 2000;
const RECONNECT_DELAY_MS = 3000;

function getTimestamp() {
  return `${new Date().toTimeString().slice(0, 8)}.${String(Date.now() % 1000).padStart(3, "0")}`;
}

function buildSocketCandidates() {
  const candidates = [
    import.meta.env.VITE_WS_URL?.trim(),
    `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${SOCKET_PATH}`,
    BACKUP_SOCKET_URL,
  ];

  return candidates.filter((candidate): candidate is string => Boolean(candidate));
}

function normalizeLevel(level: unknown): LogLevel {
  if (level === "INFO" || level === "WARN" || level === "ERROR" || level === "DEBUG") {
    return level;
  }

  return "INFO";
}

function normalizeLogEntry(payload: unknown): LogEntry | null {
  if (!payload || typeof payload !== "object") {
    if (typeof payload === "string" && payload.trim()) {
      return {
        id: `ws-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        timestamp: getTimestamp(),
        level: "INFO",
        service: "backend",
        message: payload,
      };
    }

    return null;
  }

  const record = payload as Record<string, unknown>;
  const nestedPayload = record.data ?? record.log ?? record.payload ?? record.entry;
  if (nestedPayload && nestedPayload !== payload) {
    return normalizeLogEntry(nestedPayload);
  }

  const message =
    typeof record.message === "string"
      ? record.message
      : typeof record.msg === "string"
        ? record.msg
        : typeof record.text === "string"
          ? record.text
          : "";

  if (!message) {
    return null;
  }

  return {
    id:
      typeof record.id === "string" && record.id
        ? record.id
        : `ws-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    timestamp:
      typeof record.timestamp === "string" && record.timestamp
        ? record.timestamp
        : typeof record.time === "string" && record.time
          ? record.time
          : getTimestamp(),
    level: normalizeLevel(record.level),
    service:
      typeof record.service === "string" && record.service
        ? record.service
        : typeof record.source === "string" && record.source
          ? record.source
          : "backend",
    message,
  };
}

/**
 * Unwrap a telemetry event envelope and convert `log.parsed` payloads into
 * displayable LogEntry objects. Non-log events (feature windows, system
 * status, anomaly detections) are silently filtered out.
 */
function extractLogEntries(eventData: string | ArrayBuffer | Blob): Promise<LogEntry[]> {
  if (typeof eventData === "string") {
    const trimmed = eventData.trim();
    if (!trimmed) {
      return Promise.resolve([]);
    }

    const parseTelemetryEnvelope = (value: unknown): LogEntry[] => {
      if (!value || typeof value !== "object") return [];

      const envelope = value as Record<string, unknown>;

      // Only process log.parsed telemetry events
      if (envelope.type !== "log.parsed" || !envelope.payload) return [];

      const payload = envelope.payload as Record<string, unknown>;
      const message =
        typeof payload.template === "string" && payload.template
          ? payload.template
          : typeof payload.template_text === "string" && payload.template_text
            ? payload.template_text
            : typeof payload.message === "string" && payload.message
              ? payload.message
              : typeof payload.raw_message === "string" && payload.raw_message
                ? payload.raw_message
                : `[template ${payload.template_id ?? "unknown"}]`;

      const levelRaw = typeof payload.level === "string" ? payload.level.toUpperCase() : "INFO";

      return [
        {
          id: `ws-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          timestamp:
            typeof envelope.timestamp === "string"
              ? new Date(envelope.timestamp).toTimeString().slice(0, 8) +
                "." +
                String(new Date(envelope.timestamp as string).getMilliseconds()).padStart(3, "0")
              : getTimestamp(),
          level: normalizeLevel(levelRaw),
          service:
            typeof payload.service === "string" && payload.service
              ? payload.service
              : "backend",
          message,
        },
      ];
    };

    try {
      const parsed = JSON.parse(trimmed) as unknown;

      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const record = parsed as Record<string, unknown>;
        if (record.type === "frame_update" && record.payload) {
          const framePayload = record.payload as Record<string, unknown>;
          if (Array.isArray(framePayload.events)) {
            return Promise.resolve(framePayload.events.flatMap(parseTelemetryEnvelope));
          }
        }
      }

      // Handle arrays of telemetry events
      if (Array.isArray(parsed)) {
        return Promise.resolve(parsed.flatMap(parseTelemetryEnvelope));
      }

      return Promise.resolve(parseTelemetryEnvelope(parsed));
    } catch {
      // Fallback: try parsing individual lines
      const lines = trimmed.split(/\r?\n/).filter(Boolean);
      if (lines.length > 1) {
        return Promise.resolve(
          lines.flatMap((line) => {
            try {
              return parseTelemetryEnvelope(JSON.parse(line) as unknown);
            } catch {
              const normalized = normalizeLogEntry(line);
              return normalized ? [normalized] : [];
            }
          }),
        );
      }

      const normalized = normalizeLogEntry(trimmed);
      return Promise.resolve(normalized ? [normalized] : []);
    }
  }

  if (eventData instanceof Blob) {
    return eventData.text().then((text) => extractLogEntries(text));
  }

  const text = new TextDecoder().decode(eventData);
  return extractLogEntries(text);
}

export function useLiveLogs() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [totalLogCount, setTotalLogCount] = useState(0);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState<LogLevel | "ALL">("ALL");
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [connectionUrl, setConnectionUrl] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const connectionStateRef = useRef<ConnectionState>("connecting");
  const activeCandidateRef = useRef(0);
  const highlightedIdsRef = useRef<Set<string>>(new Set());
  const cleanupTimersRef = useRef<number[]>([]);

  const socketCandidates = useMemo(buildSocketCandidates, []);

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

    const markNewEntries = (entries: LogEntry[]) => {
      if (!entries.length) {
        return;
      }

      setTotalLogCount((prev) => prev + entries.length);

      setLogs((previousLogs) => {
        const nextLogs = [...previousLogs];

        for (const entry of entries) {
          if (highlightedIdsRef.current.has(entry.id)) {
            continue;
          }

          highlightedIdsRef.current.add(entry.id);
          nextLogs.push(entry);
          setNewIds((previousIds) => {
            const nextIds = new Set(previousIds);
            nextIds.add(entry.id);
            return nextIds;
          });

          const timerId = window.setTimeout(() => {
            setNewIds((previousIds) => {
              const nextIds = new Set(previousIds);
              nextIds.delete(entry.id);
              return nextIds;
            });
            highlightedIdsRef.current.delete(entry.id);
          }, NEW_LOG_HIGHLIGHT_MS);

          cleanupTimersRef.current.push(timerId);
        }

        return nextLogs.slice(-MAX_VISIBLE_LOGS);
      });
    };

    const connect = (index: number) => {
      if (cancelled) {
        return;
      }

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
        if (cancelled || socketRef.current !== socket) {
          return;
        }

        updateConnectionState("connected");
        setConnectionUrl(candidate);
      };

      socket.onmessage = async (event) => {
        if (cancelled || socketRef.current !== socket) {
          return;
        }

        const entries = await extractLogEntries(event.data);
        markNewEntries(entries);
      };

      socket.onerror = () => {
        if (cancelled || socketRef.current !== socket) {
          return;
        }

        if (connectionStateRef.current === "connecting") {
          connect(index + 1);
          return;
        }

        updateConnectionState("error");
      };

      socket.onclose = () => {
        if (cancelled || socketRef.current !== socket) {
          return;
        }

        socketRef.current = null;

        if (!reconnectEnabled) {
          return;
        }

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
  }, [socketCandidates]);

  useEffect(() => {
    return () => {
      for (const timerId of cleanupTimersRef.current) {
        window.clearTimeout(timerId);
      }
      cleanupTimersRef.current = [];
    };
  }, []);

  const filteredLogs = filter === "ALL" ? logs : logs.filter((log) => log.level === filter);

  return {
    connectionState,
    connectionUrl,
    filter,
    filteredLogs,
    totalLogCount,
    newIds,
    paused,
    setFilter,
    setPaused,
  };
}

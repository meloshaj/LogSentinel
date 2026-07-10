import { useEffect, useRef, useState } from "react";
import type { TelemetryEvent } from "../types/telemetry";

export type TelemetryConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

const DEFAULT_WS_URL = "ws://localhost:8000/ws/telemetry";
const RECONNECT_DELAY_MS = 2000;
const MAX_RECENT_EVENTS = 30;

function getTelemetryUrl() {
  return import.meta.env.VITE_WS_URL || DEFAULT_WS_URL;
}

function parseTelemetryEvent(raw: string): TelemetryEvent | null {
  try {
    const parsed = JSON.parse(raw) as Partial<TelemetryEvent>;
    if (!parsed || typeof parsed.type !== "string" || typeof parsed.timestamp !== "string" || typeof parsed.payload !== "object") {
      return null;
    }
    return parsed as TelemetryEvent;
  } catch {
    return null;
  }
}

export function useTelemetrySocket() {
  const [connectionStatus, setConnectionStatus] = useState<TelemetryConnectionStatus>("connecting");
  const [latestEvent, setLatestEvent] = useState<TelemetryEvent | null>(null);
  const [recentEvents, setRecentEvents] = useState<TelemetryEvent[]>([]);
  const [eventCount, setEventCount] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const shouldReconnectRef = useRef(true);

  useEffect(() => {
    shouldReconnectRef.current = true;

    const clearReconnectTimer = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const connect = () => {
      clearReconnectTimer();
      setConnectionStatus("connecting");

      const socket = new WebSocket(getTelemetryUrl());
      socketRef.current = socket;

      socket.onopen = () => {
        setConnectionStatus("connected");
      };

      socket.onmessage = (message) => {
        if (typeof message.data !== "string") return;

        const event = parseTelemetryEvent(message.data);
        if (!event) return;

        setLatestEvent(event);
        setEventCount((count) => count + 1);
        setRecentEvents((events) => [event, ...events].slice(0, MAX_RECENT_EVENTS));
      };

      socket.onerror = () => {
        setConnectionStatus("error");
      };

      socket.onclose = () => {
        if (socketRef.current === socket) {
          socketRef.current = null;
        }

        if (!shouldReconnectRef.current) {
          setConnectionStatus("disconnected");
          return;
        }

        setConnectionStatus("disconnected");
        reconnectTimerRef.current = window.setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();

    return () => {
      shouldReconnectRef.current = false;
      clearReconnectTimer();

      if (socketRef.current) {
        socketRef.current.close();
        socketRef.current = null;
      }
    };
  }, []);

  return {
    connectionStatus,
    latestEvent,
    recentEvents,
    eventCount,
  };
}

import { useEffect, useState, useCallback, useRef } from "react";
import { useTelemetrySocket } from "./useTelemetrySocket";

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
    (value.blast_radius === undefined || value.blast_radius === null || isBlastRadius(value.blast_radius)) &&
    (value.suspected_root_service === undefined || value.suspected_root_service === null || typeof value.suspected_root_service === "string")
  );
}

function isPerformanceEvent(value: unknown): value is PerformanceEvent {
  if (!isRecord(value)) return false;

  return (
    typeof value.metric_name === "string" &&
    typeof value.current_value === "number" &&
    typeof value.threshold === "number" &&
    typeof value.severity === "string" &&
    (value.health_metrics === undefined || value.health_metrics === null || isRecord(value.health_metrics))
  );
}

function getFrameEvents(payload: unknown): unknown[] | null {
  if (!isRecord(payload) || !Array.isArray(payload.events)) return null;
  return payload.events;
}

export function useTelemetryStream() {
  const { latestEvent, connectionStatus } = useTelemetrySocket();

  const [activeTrackingLoops, setActiveTrackingLoops] = useState<Record<string, TrackingLoopEvent>>({});
  const [latestPerformanceEvents, setLatestPerformanceEvents] = useState<Record<string, PerformanceEvent>>({});

  const pendingTrackingUpdates = useRef<TrackingLoopEvent[]>([]);
  const pendingPerformanceUpdates = useRef<PerformanceEvent[]>([]);

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
    }, 500); // 2 updates per second

    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!latestEvent) return;

    const eventType: string = latestEvent.type;
    if (eventType === "frame_update") {
      const events = getFrameEvents(latestEvent.payload);
      if (!events) return;

      events.forEach((event) => {
        if (
          isRecord(event) &&
          event.type === "infrastructure.tracking_loop.triggered" &&
          isTrackingLoopEvent(event.payload)
        ) {
          pendingTrackingUpdates.current.push(event.payload);
        } else if (
          isRecord(event) &&
          event.type === "infrastructure.performance.alert" &&
          isPerformanceEvent(event.payload)
        ) {
          pendingPerformanceUpdates.current.push(event.payload);
        }
      });
    }
    // Fallback for non-batched events if backend hasn't fully switched
    else if (eventType === "infrastructure.tracking_loop.triggered") {
      const payload = latestEvent.payload;
      if (isTrackingLoopEvent(payload)) {
        pendingTrackingUpdates.current.push(payload);
      }
    }
    else if (eventType === "infrastructure.performance.alert") {
      const payload = latestEvent.payload;
      if (isPerformanceEvent(payload)) {
        pendingPerformanceUpdates.current.push(payload);
      }
    }

  }, [latestEvent]); // We only depend on latestEvent

  const clearTrackingLoops = useCallback(() => setActiveTrackingLoops({}), []);
  const clearPerformanceEvents = useCallback(() => setLatestPerformanceEvents({}), []);

  return {
    connectionStatus,
    activeTrackingLoops: Object.values(activeTrackingLoops),
    latestPerformanceEvents: Object.values(latestPerformanceEvents),
    clearTrackingLoops,
    clearPerformanceEvents,
  };
}

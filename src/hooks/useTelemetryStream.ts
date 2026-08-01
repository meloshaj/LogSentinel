import { useEffect, useState, useCallback } from "react";
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
  blast_radius?: { blast_radius: BlastRadiusNode[] };
  suspected_root_service?: string;
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

function isBlastRadius(value: unknown): value is { blast_radius: BlastRadiusNode[] } {
  return (
    isRecord(value) &&
    Array.isArray(value.blast_radius) &&
    value.blast_radius.every(isBlastRadiusNode)
  );
}

function isTrackingLoopEvent(value: unknown): value is TrackingLoopEvent {
  if (!isRecord(value)) return false;

  return (
    typeof value.window_id === "string" &&
    typeof value.anomaly_score === "number" &&
    typeof value.severity === "string" &&
    typeof value.status === "string" &&
    (value.blast_radius === undefined || isBlastRadius(value.blast_radius)) &&
    (value.suspected_root_service === undefined ||
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
    (value.health_metrics === undefined || isRecord(value.health_metrics))
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

  useEffect(() => {
    if (!latestEvent) return;

    const eventType: string = latestEvent.type;
    if (eventType === "frame_update") {
      const events = getFrameEvents(latestEvent.payload);
      if (!events) return;

      let hasTrackingUpdates = false;
      let hasPerformanceUpdates = false;
      // Using functional state updates to avoid dependency on current state values directly
      setActiveTrackingLoops((prev) => {
        const next = { ...prev };
        events.forEach((event) => {
          if (
            isRecord(event) &&
            event.type === "infrastructure.tracking_loop.triggered" &&
            isTrackingLoopEvent(event.payload)
          ) {
            next[event.payload.window_id] = event.payload;
            hasTrackingUpdates = true;
          }
        });
        return hasTrackingUpdates ? next : prev;
      });

      setLatestPerformanceEvents((prev) => {
        const next = { ...prev };
        events.forEach((event) => {
          if (
            isRecord(event) &&
            event.type === "infrastructure.performance.alert" &&
            isPerformanceEvent(event.payload)
          ) {
            next[event.payload.metric_name] = event.payload;
            hasPerformanceUpdates = true;
          }
        });
        return hasPerformanceUpdates ? next : prev;
      });
    }
    // Fallback for non-batched events if backend hasn't fully switched
    else if (eventType === "infrastructure.tracking_loop.triggered") {
      const payload = latestEvent.payload;
      if (isTrackingLoopEvent(payload)) {
        setActiveTrackingLoops(prev => ({ ...prev, [payload.window_id]: payload }));
      }
    }
    else if (eventType === "infrastructure.performance.alert") {
      const payload = latestEvent.payload;
      if (isPerformanceEvent(payload)) {
        setLatestPerformanceEvents(prev => ({ ...prev, [payload.metric_name]: payload }));
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

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
  health_metrics?: any;
};

export function useTelemetryStream() {
  const { latestEvent, connectionStatus } = useTelemetrySocket();
  
  const [activeTrackingLoops, setActiveTrackingLoops] = useState<Record<string, TrackingLoopEvent>>({});
  const [latestPerformanceEvents, setLatestPerformanceEvents] = useState<Record<string, PerformanceEvent>>({});

  useEffect(() => {
    if (!latestEvent) return;

    if (latestEvent.type === "frame_update" && Array.isArray(latestEvent.payload?.events)) {
      const events = latestEvent.payload.events;
      
      let hasTrackingUpdates = false;
      let hasPerformanceUpdates = false;
      // Using functional state updates to avoid dependency on current state values directly
      setActiveTrackingLoops((prev) => {
        const next = { ...prev };
        events.forEach((evt: any) => {
          if (evt.type === "infrastructure.tracking_loop.triggered") {
            const payload = evt.payload as TrackingLoopEvent;
            if (payload.window_id) {
              next[payload.window_id] = payload;
              hasTrackingUpdates = true;
            }
          }
        });
        return hasTrackingUpdates ? next : prev;
      });

      setLatestPerformanceEvents((prev) => {
        const next = { ...prev };
        events.forEach((evt: any) => {
          if (evt.type === "infrastructure.performance.alert") {
            const payload = evt.payload as PerformanceEvent;
            if (payload.metric_name) {
              next[payload.metric_name] = payload;
              hasPerformanceUpdates = true;
            }
          }
        });
        return hasPerformanceUpdates ? next : prev;
      });
    } 
    // Fallback for non-batched events if backend hasn't fully switched
    else if (latestEvent.type === "infrastructure.tracking_loop.triggered") {
      const payload = latestEvent.payload as TrackingLoopEvent;
      if (payload.window_id) {
        setActiveTrackingLoops(prev => ({ ...prev, [payload.window_id]: payload }));
      }
    }
    else if (latestEvent.type === "infrastructure.performance.alert") {
      const payload = latestEvent.payload as PerformanceEvent;
      if (payload.metric_name) {
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

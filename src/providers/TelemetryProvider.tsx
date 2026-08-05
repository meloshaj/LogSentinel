import React, { createContext, useContext, useEffect, useState, useRef, useCallback } from 'react';
import { useTelemetrySocket, TelemetryConnectionStatus } from '../hooks/useTelemetrySocket';
import type { TrackingLoopEvent, PerformanceEvent } from '../types/telemetryEvents';

// Re-implement useTelemetryStream logic here to centralize it

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isBlastRadiusNode(value: unknown) {
  if (!isRecord(value)) return false;
  return typeof value.service_name === 'string' &&
    (value.impact_classification === 'root' || value.impact_classification === 'direct' || value.impact_classification === 'indirect') &&
    isStringArray(value.dependency_path) && isStringArray(value.propagation_path) && typeof value.impact_score === 'number';
}

function isBlastRadius(value: unknown) {
  return Array.isArray(value) && value.every(isBlastRadiusNode);
}

function isTrackingLoopEvent(value: unknown): value is TrackingLoopEvent {
  if (!isRecord(value)) return false;
  return typeof value.window_id === 'string' && typeof value.anomaly_score === 'number' && typeof value.severity === 'string' && typeof value.status === 'string' &&
    (value.blast_radius === undefined || value.blast_radius === null || isBlastRadius(value.blast_radius)) &&
    (value.suspected_root_service === undefined || value.suspected_root_service === null || typeof value.suspected_root_service === 'string');
}

function isPerformanceEvent(value: unknown): value is PerformanceEvent {
  if (!isRecord(value)) return false;
  return typeof value.metric_name === 'string' && typeof value.current_value === 'number' && typeof value.threshold === 'number' && typeof value.severity === 'string' &&
    (value.health_metrics === undefined || value.health_metrics === null || isRecord(value.health_metrics));
}

function getFrameEvents(payload: unknown): unknown[] | null {
  if (!isRecord(payload) || !Array.isArray(payload.events)) return null;
  return payload.events;
}

interface TelemetryContextType {
  connectionStatus: TelemetryConnectionStatus;
  activeTrackingLoops: TrackingLoopEvent[];
  latestPerformanceEvents: PerformanceEvent[];
  clearTrackingLoops: () => void;
  clearPerformanceEvents: () => void;
}

export const TelemetryContext = createContext<TelemetryContextType | null>(null);

export function TelemetryProvider({ children }: { children: React.ReactNode }) {
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
          pendingTrackingUpdates.current.forEach((e) => { next[e.window_id] = e; });
          return next;
        });
        pendingTrackingUpdates.current = [];
      }
      if (pendingPerformanceUpdates.current.length > 0) {
        setLatestPerformanceEvents((prev) => {
          const next = { ...prev };
          pendingPerformanceUpdates.current.forEach((e) => { next[e.metric_name] = e; });
          return next;
        });
        pendingPerformanceUpdates.current = [];
      }
    }, 500);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!latestEvent) return;
    const eventType: string = latestEvent.type;
    if (eventType === 'frame_update') {
      const events = getFrameEvents(latestEvent.payload);
      if (!events) return;
      events.forEach((event) => {
        if (isRecord(event) && event.type === 'infrastructure.tracking_loop.triggered' && isTrackingLoopEvent(event.payload)) {
          pendingTrackingUpdates.current.push(event.payload);
        } else if (isRecord(event) && event.type === 'infrastructure.performance.alert' && isPerformanceEvent(event.payload)) {
          pendingPerformanceUpdates.current.push(event.payload);
        }
      });
    } else if (eventType === 'infrastructure.tracking_loop.triggered') {
      const payload = latestEvent.payload;
      if (isTrackingLoopEvent(payload)) { pendingTrackingUpdates.current.push(payload); }
    } else if (eventType === 'infrastructure.performance.alert') {
      const payload = latestEvent.payload;
      if (isPerformanceEvent(payload)) { pendingPerformanceUpdates.current.push(payload); }
    }
  }, [latestEvent]);

  const clearTrackingLoops = useCallback(() => setActiveTrackingLoops({}), []);
  const clearPerformanceEvents = useCallback(() => setLatestPerformanceEvents({}), []);

  return (
    <TelemetryContext.Provider value={{ connectionStatus, activeTrackingLoops: Object.values(activeTrackingLoops), latestPerformanceEvents: Object.values(latestPerformanceEvents), clearTrackingLoops, clearPerformanceEvents }}>
      {children}
    </TelemetryContext.Provider>
  );
}

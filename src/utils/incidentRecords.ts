import type { Incident } from "../types/monitoring";
import type {
  PerformanceEvent,
  TrackingLoopEvent,
} from "../providers/TelemetryProvider";
import { resolveRootService } from "./incident";

export type IncidentRecord = Incident &
  Omit<Partial<TrackingLoopEvent>, "id" | "status"> & { backendId?: number };

/** Build the exact loop and performance records shown by the Incidents page. */
export function deriveIncidentRecords(
  activeTrackingLoops: TrackingLoopEvent[],
  latestPerformanceEvents: PerformanceEvent[],
): IncidentRecord[] {
  return [
    ...activeTrackingLoops.map((loop) => {
      const rootService = resolveRootService(loop);
      return {
        ...loop,
        id: String(loop.id ?? loop.window_id),
        backendId: loop.id,
        service: rootService || "Root cause unavailable",
        severity: (
          loop.severity === "medium" ||
          loop.severity === "low" ||
          loop.severity === "high" ||
          loop.severity === "critical"
            ? loop.severity
            : "medium"
        ) as Incident["severity"],
        timestamp: loop.created_at
          ? new Date(loop.created_at).toLocaleTimeString()
          : "Time unavailable",
        description: `Anomaly loop detected with score ${loop.anomaly_score.toFixed(2)} across dependency cascade.`,
        status: (
          loop.status === "open" ||
          loop.status === "acknowledged" ||
          loop.status === "investigating" ||
          loop.status === "resolved"
            ? loop.status
            : "open"
        ) as Incident["status"],
      };
    }),
    ...latestPerformanceEvents.map((event) => ({
      id: event.metric_name,
      service: "infrastructure",
      severity: (
        event.severity === "medium" ||
        event.severity === "low" ||
        event.severity === "high" ||
        event.severity === "critical"
          ? event.severity
          : "medium"
      ) as Incident["severity"],
      timestamp: "Time unavailable",
      description: `Performance alert: ${event.metric_name} is ${event.current_value.toFixed(0)} (threshold ${event.threshold})`,
      status: "open" as const,
    })),
  ];
}

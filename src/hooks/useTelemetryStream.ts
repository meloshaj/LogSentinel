import { useCallback } from "react";
import { useTelemetryContext } from "../providers/TelemetryProvider";
import type { TelemetryConnectionStatus } from "./useTelemetrySocket";

// Re-export types so existing consumers don't break their imports.
export type {
  BlastRadiusNode,
  TrackingLoopEvent,
  PerformanceEvent,
} from "../providers/TelemetryProvider";

/**
 * useTelemetryStream — thin hook that reads tracking-loop and performance
 * events from the global TelemetryProvider instead of owning its own state.
 */
export function useTelemetryStream() {
  const {
    connectionState,
    activeTrackingLoops,
    latestPerformanceEvents,
    clearTrackingLoops,
    clearPerformanceEvents,
  } = useTelemetryContext();

  // Map the provider's ConnectionState to the original TelemetryConnectionStatus type.
  const connectionStatus: TelemetryConnectionStatus = connectionState;

  return {
    connectionStatus,
    activeTrackingLoops,
    latestPerformanceEvents,
    clearTrackingLoops,
    clearPerformanceEvents,
  };
}

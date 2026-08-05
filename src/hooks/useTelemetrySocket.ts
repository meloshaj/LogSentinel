import { useTelemetryContext } from "../providers/TelemetryProvider";
import type { TelemetryEvent } from "../types/telemetry";

export type TelemetryConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

/**
 * useTelemetrySocket — thin hook that exposes raw telemetry events from
 * the global TelemetryProvider.
 *
 * Previously this hook owned its own WebSocket connection. Now it reads
 * from the shared provider so only one WebSocket exists per session.
 */
export function useTelemetrySocket() {
  const {
    connectionState,
    latestEvent,
    recentEvents,
    eventCount,
  } = useTelemetryContext();

  const connectionStatus: TelemetryConnectionStatus = connectionState;

  return {
    connectionStatus,
    latestEvent,
    recentEvents,
    eventCount,
  };
}

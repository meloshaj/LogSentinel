import { useState } from "react";
import type { LogLevel } from "../types/monitoring";
import { useTelemetryContext } from "../providers/TelemetryProvider";

/**
 * useLiveLogs — thin hook that reads logs from the global TelemetryProvider.
 *
 * The WebSocket connection and log buffer now live inside TelemetryProvider,
 * so navigating between dashboard pages no longer resets the log stream.
 *
 * Local UI state (filter, paused) is still per-component to avoid coupling
 * unrelated page states together.
 */
export function useLiveLogs() {
  const {
    logs,
    newIds,
    connectionState,
    connectionUrl,
  } = useTelemetryContext();

  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState<LogLevel | "ALL">("ALL");

  const filteredLogs = filter === "ALL" ? logs : logs.filter((log) => log.level === filter);

  return {
    connectionState,
    connectionUrl,
    filter,
    filteredLogs,
    newIds,
    paused,
    setFilter,
    setPaused,
  };
}

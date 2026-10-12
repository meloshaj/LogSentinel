import type { ReactNode } from "react";
import type { OperationalDataState, OperationalStatus } from "../../types/operational";

type ConnectionLike =
  | "connecting"
  | "live"
  | "reconnecting"
  | "offline"
  | "stale"
  | "auth_required"
  | "failed";

const DISCONNECTED_CONNECTIONS = new Set<ConnectionLike>([
  "reconnecting",
  "offline",
  "stale",
  "auth_required",
  "failed",
]);

export function displayOperationalStatus<T>(
  state: OperationalDataState<T>,
  connectionState?: ConnectionLike,
): OperationalStatus {
  if (!connectionState || connectionState === "live" || connectionState === "connecting") {
    return state.status;
  }
  if (state.status === "available" || state.status === "stale") return "stale";
  if (state.status === "empty") return "unavailable";
  return state.status;
}

export function operationalStatusLabel(status: OperationalStatus): string {
  switch (status) {
    case "loading":
      return "Loading";
    case "available":
      return "Current";
    case "empty":
      return "No data yet";
    case "stale":
      return "Stale";
    case "unavailable":
    case "error":
      return "Unavailable";
  }
}

export function OperationalStateNotice({
  state,
  connectionState,
  emptyMessage = "No data was returned.",
  errorMessage = "This data is currently unavailable.",
  className = "",
  children,
}: {
  state: OperationalDataState<unknown>;
  connectionState?: ConnectionLike;
  emptyMessage?: string;
  errorMessage?: string;
  className?: string;
  children?: ReactNode;
}) {
  const status = displayOperationalStatus(state, connectionState);
  if (status === "available") return null;

  const message =
    status === "loading"
      ? "Loading current data…"
      : status === "empty"
        ? emptyMessage
        : status === "stale"
          ? "Showing last known data — refresh is required for current truth."
          : errorMessage;

  return (
    <div
      role={status === "error" || status === "unavailable" ? "alert" : "status"}
      aria-live="polite"
      className={`flex items-center gap-2 rounded-lg border border-[#30363d] bg-[#0d1117]/80 px-3 py-2 text-xs text-[#8b949e] ${className}`}
    >
      <span className="font-semibold text-[#c9d1d9]">{operationalStatusLabel(status)}</span>
      <span>{message}</span>
      {state.lastUpdated && status === "stale" ? (
        <time className="ml-auto shrink-0 font-mono text-[10px]" dateTime={state.lastUpdated}>
          last update {new Date(state.lastUpdated).toLocaleTimeString()}
        </time>
      ) : null}
      {children}
    </div>
  );
}

export function formatOperationalValue(
  state: OperationalDataState<unknown>,
  value: string | number,
  connectionState?: ConnectionLike,
): string | number {
  const status = displayOperationalStatus(state, connectionState);
  return status === "available" || status === "empty" || status === "stale" ? value : operationalStatusLabel(status);
}

export { DISCONNECTED_CONNECTIONS };

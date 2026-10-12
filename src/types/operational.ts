/**
 * Canonical state for data that can be displayed as operational truth.
 *
 * A failed refresh never becomes an empty array or a healthy-looking default:
 * callers either receive a real value, a real empty value, or an explicit
 * unavailable/error state.  Previous data is represented as `stale`.
 */
export type OperationalStatus =
  | "loading"
  | "available"
  | "empty"
  | "stale"
  | "unavailable"
  | "error";

export type OperationalDataState<T> =
  | {
      status: "loading";
      data: null;
      lastUpdated: null;
      source: string;
      error: null;
    }
  | {
      status: "available" | "empty" | "stale";
      data: T;
      lastUpdated: string;
      source: string;
      error: string | null;
    }
  | {
      status: "unavailable" | "error";
      data: null;
      lastUpdated: null;
      source: string;
      error: string;
    };

export function loadingState<T>(source: string): OperationalDataState<T> {
  return { status: "loading", data: null, lastUpdated: null, source, error: null };
}

export function successState<T>(
  data: T,
  lastUpdated: string,
  source: string,
  isEmpty: boolean,
): OperationalDataState<T> {
  return {
    status: isEmpty ? "empty" : "available",
    data,
    lastUpdated,
    source,
    error: null,
  };
}

export function staleState<T>(
  data: T,
  lastUpdated: string,
  source: string,
  error: string | null = null,
): OperationalDataState<T> {
  return { status: "stale", data, lastUpdated, source, error };
}

export function unavailableState<T>(
  source: string,
  error: string,
): OperationalDataState<T> {
  return { status: "error", data: null, lastUpdated: null, source, error };
}

export type RealtimeStatus =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "failed";

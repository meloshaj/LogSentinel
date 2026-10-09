import { useCallback, useEffect, useRef, useState } from "react";
import { fetchAuthenticated } from "../utils/auth";
import {
  loadingState,
  staleState,
  successState,
  unavailableState,
  type OperationalDataState,
} from "../types/operational";

export type WorkerRole = "pipeline" | "webhook" | "archive";
export type WorkerStatus = "healthy" | "stale" | "unavailable";

export interface WorkerHealth {
  status: WorkerStatus;
  heartbeat_at: string | null;
  age_seconds: number | null;
}

export interface WorkerHealthPayload {
  generated_at: string;
  workers: Record<WorkerRole, WorkerHealth>;
}

const WORKER_ROLES: WorkerRole[] = ["pipeline", "webhook", "archive"];
const POLL_INTERVAL_MS = 30_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseWorkerHealth(value: unknown): WorkerHealthPayload {
  if (!isRecord(value) || typeof value.generated_at !== "string" || !Number.isFinite(new Date(value.generated_at).getTime()) || !isRecord(value.workers)) {
    throw new Error("Worker health response schema invalid");
  }

  const workers = {} as Record<WorkerRole, WorkerHealth>;
  for (const role of WORKER_ROLES) {
    const raw = value.workers[role];
    if (!isRecord(raw) || (raw.status !== "healthy" && raw.status !== "stale" && raw.status !== "unavailable")) {
      throw new Error("Worker health response schema invalid");
    }
    const heartbeat = raw.heartbeat_at;
    if (heartbeat !== null && (typeof heartbeat !== "string" || !Number.isFinite(new Date(heartbeat).getTime()))) {
      throw new Error("Worker health response schema invalid");
    }
    const age = raw.age_seconds;
    if (age !== null && (typeof age !== "number" || !Number.isFinite(age) || age < 0)) {
      throw new Error("Worker health response schema invalid");
    }
    workers[role] = {
      status: raw.status,
      heartbeat_at: heartbeat,
      age_seconds: age,
    };
  }
  return {
    generated_at: new Date(value.generated_at).toISOString(),
    workers,
  };
}

export function useWorkerHealth(pollIntervalMs = POLL_INTERVAL_MS): OperationalDataState<WorkerHealthPayload> {
  const [state, setState] = useState<OperationalDataState<WorkerHealthPayload>>(
    loadingState<WorkerHealthPayload>("GET /api/v1/worker-health"),
  );
  const previousRef = useRef<WorkerHealthPayload | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await fetchAuthenticated("/api/v1/worker-health", { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = parseWorkerHealth(await response.json());
      if (controller.signal.aborted) return;
      previousRef.current = payload;
      setState(successState(payload, payload.generated_at, "GET /api/v1/worker-health", false));
    } catch {
      if (controller.signal.aborted) return;
      const message = "Worker heartbeat state is currently unavailable";
      if (previousRef.current) {
        setState(staleState(previousRef.current, previousRef.current.generated_at, "GET /api/v1/worker-health", message));
      } else {
        setState(unavailableState("GET /api/v1/worker-health", message));
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), pollIntervalMs);
    return () => {
      window.clearInterval(timer);
      abortRef.current?.abort();
    };
  }, [pollIntervalMs, refresh]);

  return state;
}

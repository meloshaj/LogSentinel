import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import { TelemetryProvider, useTelemetryContext, deduplicateAndMerge } from "../TelemetryProvider";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import type { LogEntry } from "../../types/monitoring";

// ---------------------------------------------------------------------------
// Mocking
// ---------------------------------------------------------------------------

let mockWsInstances: MockWebSocket[] = [];

class MockWebSocket {
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  onerror?: () => void;
  onclose?: () => void;
  readyState = 1; // OPEN

  constructor(public url: string) {
    mockWsInstances.push(this);
    // Simulate connection opening shortly after instantiation
    setTimeout(() => {
      if (this.onopen) this.onopen();
    }, 10);
  }
  close() {}
  send() {}
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockWsInstances = [];
  fetchMock = vi.fn();
  (globalThis as any).fetch = fetchMock;
  (globalThis as any).WebSocket = MockWebSocket;
  localStorage.setItem("authToken", "test-token");
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Test Utilities
// ---------------------------------------------------------------------------

const TestConsumer = () => {
  const { logs, isBackfillLoading, backfillError } = useTelemetryContext();
  return (
    <div>
      <div data-testid="loading">{isBackfillLoading ? "true" : "false"}</div>
      <div data-testid="error">{backfillError || "none"}</div>
      <div data-testid="log-count">{logs.length}</div>
      <div data-testid="log-ids">{logs.map((l) => l.id).join(",")}</div>
      <ul data-testid="logs">
        {logs.map((log) => (
          <li key={log.id}>
            {log.id}_{log.service}_{log.message}
          </li>
        ))}
      </ul>
    </div>
  );
};

/**
 * Send a WebSocket `log.parsed` event with a specific ULID.
 * The `id` field now comes from the backend payload, not from the frontend.
 */
const sendWsLog = (id: string, message: string, timestamp: string) => {
  const ws = mockWsInstances[0];
  if (ws && ws.onmessage) {
    ws.onmessage({
      data: JSON.stringify({
        type: "log.parsed",
        timestamp: timestamp,
        payload: {
          id: id,
          timestamp: timestamp,
          service: "backend",
          level: "INFO",
          message: message,
          raw_message: message,
        },
      }),
    });
  }
};

/**
 * Create a REST log record with the given ULID.
 */
const createRestLog = (id: string, message: string, timestamp: string) => ({
  id: id,
  timestamp: timestamp,
  service: "backend",
  level: "INFO",
  message: message,
  raw_message: message,
});

// ---------------------------------------------------------------------------
// Unit Tests: deduplicateAndMerge
// ---------------------------------------------------------------------------

describe("deduplicateAndMerge (ULID-based)", () => {
  it("deduplicates entries with the same id across sources", () => {
    const sharedId = "01J4Q3R5MXABCDEF12345678";
    const a: LogEntry[] = [
      { id: sharedId, timestamp: "12:00:00.000", level: "INFO", service: "svc-a", message: "hello" },
    ];
    const b: LogEntry[] = [
      { id: sharedId, timestamp: "12:00:00.000", level: "INFO", service: "svc-a", message: "hello" },
      { id: "01J4Q3R6NXABCDEF12345679", timestamp: "12:00:01.000", level: "WARN", service: "svc-b", message: "world" },
    ];

    const result = deduplicateAndMerge(a, b);
    expect(result).toHaveLength(2);
    const ids = result.map((r) => r.id);
    expect(ids).toContain(sharedId);
    expect(ids).toContain("01J4Q3R6NXABCDEF12345679");
  });

  it("sorts descending by ULID (newest first)", () => {
    const older = "01J4Q3R5MXABCDEF12345670";
    const newer = "01J4Q3R6NXABCDEF12345671";
    const logs: LogEntry[] = [
      { id: older, timestamp: "12:00:00.000", level: "INFO", service: "s", message: "old" },
      { id: newer, timestamp: "12:00:01.000", level: "INFO", service: "s", message: "new" },
    ];

    const result = deduplicateAndMerge(logs);
    expect(result[0].id).toBe(newer);
    expect(result[1].id).toBe(older);
  });

  it("caps output at 500 entries", () => {
    const logs: LogEntry[] = Array.from({ length: 600 }).map((_, i) => ({
      id: `01J4Q3R${String(i).padStart(4, "0")}ABCDEF1234`,
      timestamp: "12:00:00.000",
      level: "INFO" as const,
      service: "s",
      message: `msg-${i}`,
    }));

    const result = deduplicateAndMerge(logs);
    expect(result).toHaveLength(500);
  });
});

// ---------------------------------------------------------------------------
// Integration Tests: TelemetryProvider
// ---------------------------------------------------------------------------

describe("TelemetryProvider", () => {
  it("attaches the current JWT to protected REST backfill and WebSocket requests", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ logs: [] }),
    });

    render(
      <TelemetryProvider>
        <TestConsumer />
      </TelemetryProvider>,
    );

    act(() => {
      vi.advanceTimersByTime(20);
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    for (const call of fetchMock.mock.calls) {
      const headers = call[1]?.headers as Headers;
      expect(headers.get("Authorization")).toBe("Bearer test-token");
    }

    expect(mockWsInstances).toHaveLength(1);
    const socketUrl = new URL(mockWsInstances[0].url);
    expect(socketUrl.searchParams.has("token")).toBe(false);
    expect(mockWsInstances[0].url).not.toContain("Bearer");
  });

  it("Test 1: Deduplication when REST and WebSocket push logs with the same ULID", async () => {
    const SHARED_ULID = "01J4Q3R5MXABCDEF12345678";

    // REST API returns 2 logs, one has the shared ULID
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        logs: [
          createRestLog(SHARED_ULID, "duplicate log", "2026-08-05T12:00:00.000Z"),
          createRestLog("01J4Q3R4LXABCDEF12345677", "rest only log", "2026-08-05T11:59:00.000Z"),
        ],
      }),
    });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });

    render(
      <TelemetryProvider>
        <TestConsumer />
      </TelemetryProvider>
    );

    // Wait for WS to initialize
    act(() => {
      vi.advanceTimersByTime(20);
    });

    // Push WS logs BEFORE backfill completes — one shares the exact same ULID
    act(() => {
      sendWsLog(SHARED_ULID, "duplicate log", "2026-08-05T12:00:00.000Z");
      sendWsLog("01J4Q3R7PXABCDEF12345680", "ws only log", "2026-08-05T12:01:00.000Z");
    });

    // Allow backfill promise to resolve
    await waitFor(() => {
      expect(screen.getByTestId("loading").textContent).toBe("false");
    });

    // Total logs should be 3 (shared ULID deduplicated, 1 rest-only, 1 ws-only)
    expect(screen.getByTestId("log-count").textContent).toBe("3");

    const logIds = screen.getByTestId("log-ids").textContent!;
    expect(logIds).toContain(SHARED_ULID);
    expect(logIds).toContain("01J4Q3R4LXABCDEF12345677");
    expect(logIds).toContain("01J4Q3R7PXABCDEF12345680");
  });

  it("Test 2: Race condition — 10 WS logs arrive before REST resolves", async () => {
    let resolveBackfill: (value: any) => void;
    const backfillPromise = new Promise((resolve) => {
      resolveBackfill = resolve;
    });

    fetchMock.mockReturnValue(backfillPromise);

    render(
      <TelemetryProvider>
        <TestConsumer />
      </TelemetryProvider>
    );

    act(() => {
      vi.advanceTimersByTime(20);
    });

    expect(screen.getByTestId("loading").textContent).toBe("true");

    // WS receives 10 logs while REST is pending
    act(() => {
      for (let i = 0; i < 10; i++) {
        sendWsLog(
          `01J4Q3R${String(i).padStart(4, "0")}ABCDEF1234`,
          `ws race log ${i}`,
          `2026-08-05T12:02:0${i}.000Z`
        );
      }
    });

    // No logs rendered yet — they are buffered in wsBufferRef
    expect(screen.getByTestId("log-count").textContent).toBe("0");

    // Now REST resolves with 5 logs (non-overlapping ULIDs)
    await act(async () => {
      resolveBackfill!({
        ok: true,
        json: async () => ({
          logs: Array.from({ length: 5 }).map((_, i) =>
            createRestLog(
              `01J4Q3RAAA${String(i).padStart(4, "0")}BCDEF1234`,
              `rest log ${i}`,
              `2026-08-05T12:00:0${i}.000Z`
            )
          ),
        }),
      });
      await Promise.resolve();
    });

    // Wait for the merge to apply
    await waitFor(() => {
      expect(screen.getByTestId("loading").textContent).toBe("false");
    });

    // Should have 15 logs total, securely merged (10 WS + 5 REST, no overlap)
    expect(screen.getByTestId("log-count").textContent).toBe("15");
  });

  it("Test 3: Strict enforcement of the 500-item rolling buffer limit", async () => {
    // REST returns 400 logs
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        logs: Array.from({ length: 400 }).map((_, i) =>
          createRestLog(
            `01J4Q3REST${String(i).padStart(4, "0")}DEF12345`,
            `rest log ${i}`,
            `2026-08-05T10:00:00.000Z`
          )
        ),
      }),
    });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });

    render(
      <TelemetryProvider>
        <TestConsumer />
      </TelemetryProvider>
    );

    act(() => {
      vi.advanceTimersByTime(20);
    });

    // Wait for backfill to finish
    await waitFor(() => {
      expect(screen.getByTestId("loading").textContent).toBe("false");
    });

    // Push 200 logs via WS (unique ULIDs)
    act(() => {
      for (let i = 0; i < 200; i++) {
        sendWsLog(
          `01J4Q3WSSS${String(i).padStart(4, "0")}DEF12345`,
          `ws limit log ${i}`,
          `2026-08-05T12:00:00.000Z`
        );
      }
      // Advance by WS flush interval
      vi.advanceTimersByTime(150);
    });

    // Total should be strictly capped at 500
    expect(screen.getByTestId("log-count").textContent).toBe("500");
  });

  it("Test 4: System resilience when REST throws a 500 error", async () => {
    // REST throws a 500 error
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
    });

    render(
      <TelemetryProvider>
        <TestConsumer />
      </TelemetryProvider>
    );

    act(() => {
      vi.advanceTimersByTime(20);
    });

    // Push a WS log while backfill is failing
    act(() => {
      sendWsLog(
        "01J4Q3R5MXABCDEF99999999",
        "ws log during fail",
        "2026-08-05T12:05:00.000Z"
      );
    });

    // Wait for backfill to reject and set error state
    await waitFor(() => {
      expect(screen.getByTestId("loading").textContent).toBe("false");
    });

    // Should show error
    const errorText = screen.getByTestId("error").textContent;
    expect(errorText).toContain("HTTP 500: Internal Server Error");

    // WS logs buffered during the fail should still flush and render
    expect(screen.getByTestId("log-count").textContent).toBe("1");

    // Post-fail WS logs should continue streaming correctly
    act(() => {
      sendWsLog(
        "01J4Q3R6NXABCDEF99999998",
        "ws log after fail",
        "2026-08-05T12:06:00.000Z"
      );
      vi.advanceTimersByTime(150);
    });

    expect(screen.getByTestId("log-count").textContent).toBe("2");
  });

  it("Test 5: Identical ULIDs from both REST and WS resolve to exactly one entry", async () => {
    const ULID_A = "01J4Q3RAAAABCDEF12345670";
    const ULID_B = "01J4Q3RBBBABCDEF12345671";

    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        logs: [
          createRestLog(ULID_A, "shared message A", "2026-08-05T12:00:00.000Z"),
          createRestLog(ULID_B, "shared message B", "2026-08-05T12:00:01.000Z"),
        ],
      }),
    });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });

    render(
      <TelemetryProvider>
        <TestConsumer />
      </TelemetryProvider>
    );

    act(() => {
      vi.advanceTimersByTime(20);
    });

    // WS sends both identical ULIDs before backfill resolves
    act(() => {
      sendWsLog(ULID_A, "shared message A", "2026-08-05T12:00:00.000Z");
      sendWsLog(ULID_B, "shared message B", "2026-08-05T12:00:01.000Z");
    });

    await waitFor(() => {
      expect(screen.getByTestId("loading").textContent).toBe("false");
    });

    // Exactly 2 entries — both ULIDs deduplicated to one each
    expect(screen.getByTestId("log-count").textContent).toBe("2");
    const ids = screen.getByTestId("log-ids").textContent!;
    expect(ids.split(",")).toHaveLength(2);
    expect(ids).toContain(ULID_A);
    expect(ids).toContain(ULID_B);
  });
});

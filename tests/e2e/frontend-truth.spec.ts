import { expect, test, type Page, type Route } from "@playwright/test";

const INTERNAL_TOKEN = "header.eyJleHAiOjQxMDAwMDAwMDB9.signature";
const SNAPSHOT_TIME = "2026-09-16T10:00:00.000Z";

type SocketMode = "connected" | "disconnected" | "reconnecting";

type FixtureOptions = {
  logs?: unknown[];
  logsStatus?: number;
  logsTimeout?: boolean;
  trackingLoops?: unknown[];
  trackingStatus?: number;
  topology?: unknown;
  topologyStatus?: number;
  workers?: Record<string, unknown>;
  workerStatus?: number;
  refreshStatus?: number;
  socketMode?: SocketMode;
  socketEvent?: unknown;
};

const emptyTopology = {
  snapshot_timestamp: SNAPSHOT_TIME,
  nodes: [],
  edges: [],
};

const healthyTopology = {
  snapshot_timestamp: SNAPSHOT_TIME,
  nodes: [
    {
      id: "svc_api",
      name: "api",
      type: "service",
      status: "healthy",
      metrics: { latency_p95_ms: 42, error_rate_pct: 0, throughput_rps: 3 },
      active_anomaly_id: null,
      is_root_cause: false,
    },
  ],
  edges: [],
};

const healthyWorkers = {
  pipeline: { status: "healthy", heartbeat_at: SNAPSHOT_TIME, age_seconds: 1 },
  webhook: { status: "healthy", heartbeat_at: SNAPSHOT_TIME, age_seconds: 2 },
  archive: { status: "healthy", heartbeat_at: SNAPSHOT_TIME, age_seconds: 3 },
};

const staleWorkers = {
  pipeline: { status: "stale", heartbeat_at: "2026-09-16T09:58:00.000Z", age_seconds: 121 },
  webhook: { status: "healthy", heartbeat_at: SNAPSHOT_TIME, age_seconds: 2 },
  archive: { status: "unavailable", heartbeat_at: null, age_seconds: null },
};

function installMockSocket(page: Page, mode: SocketMode, event?: unknown) {
  return page.addInitScript(({ socketMode, socketEvent }) => {
    class MockWebSocket {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSING = 2;
      static readonly CLOSED = 3;
      readonly url: string;
      readyState = MockWebSocket.CONNECTING;
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      onclose: ((event: { code: number }) => void) | null = null;
      private closed = false;

      constructor(url: string) {
        this.url = url;
        window.setTimeout(() => {
          if (this.closed) return;
          if (socketMode === "connected" || socketMode === "disconnected" || socketMode === "reconnecting") {
            this.readyState = MockWebSocket.OPEN;
            this.onopen?.();
            if (socketEvent) this.onmessage?.({ data: JSON.stringify(socketEvent) });
            if (socketMode === "disconnected" || socketMode === "reconnecting") {
              window.setTimeout(() => this.close(1006), 120);
            }
          }
        }, 20);
      }

      send(_data: string) {}

      close(code = 1000) {
        if (this.closed) return;
        this.closed = true;
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.({ code });
      }
    }

    Object.defineProperty(window, "WebSocket", {
      configurable: true,
      writable: true,
      value: MockWebSocket,
    });
  }, { socketMode: mode, socketEvent: event });
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function openDashboard(page: Page, options: FixtureOptions = {}) {
  const {
    logs = [],
    logsStatus = 200,
    logsTimeout = false,
    trackingLoops = [],
    trackingStatus = 200,
    topology = emptyTopology,
    topologyStatus = 200,
    workers = healthyWorkers,
    workerStatus = 200,
    refreshStatus = 200,
    socketMode = "connected",
    socketEvent,
  } = options;

  await installMockSocket(page, socketMode, socketEvent);
  await page.context().addCookies([
    { name: "logsentinel_csrf", value: "browser-test-csrf", url: "http://127.0.0.1:5173" },
  ]);
  await page.route("**/api/auth/refresh", (route) => fulfillJson(route, refreshStatus, { access_token: INTERNAL_TOKEN }));
  await page.route("**/api/v1/logs/recent*", async (route) => {
    if (logsTimeout) {
      await route.abort("timedout");
      return;
    }
    await fulfillJson(route, logsStatus, { logs });
  });
  await page.route("**/api/v1/tracking-loops*", (route) => fulfillJson(route, trackingStatus, trackingLoops));
  await page.route("**/api/v1/topology", (route) => fulfillJson(route, topologyStatus, topology));
  await page.route("**/api/v1/worker-health", (route) => fulfillJson(route, workerStatus, { generated_at: SNAPSHOT_TIME, workers }));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Observability Overview" })).toBeVisible();
  await expect(page.getByText("Logs Processed")).toBeVisible();
}

const realZeroLog = {
  id: "01J8ZEROLOG0000000000000000",
  timestamp: SNAPSHOT_TIME,
  service: "api",
  level: "INFO",
  message: "zero-state fixture",
};

const realLog = {
  id: "01J8NONZEROLOG0000000000000",
  timestamp: SNAPSHOT_TIME,
  service: "api",
  level: "INFO",
  message: "current-state fixture",
  latency_ms: 42,
};

const anomalyLoop = {
  id: 7,
  window_id: "window-e2e-1",
  anomaly_score: 0.9,
  severity: "critical",
  status: "triggered",
  created_at: SNAPSHOT_TIME,
  blast_radius: [],
};

test.describe("frontend operational truth", () => {
  test("keeps real zero distinct from unavailable and renders a true empty result", async ({ page }) => {
    await openDashboard(page, { logs: [], trackingLoops: [], topology: emptyTopology });

    await expect(page.getByText("No telemetry was returned for this scope.")).toBeVisible();
    await expect(page.getByText("Logs Processed").locator("..").getByText("0", { exact: true })).toBeVisible();
    await expect(page.getByText("All reported healthy", { exact: true })).toHaveCount(0);
  });

  test("renders nonzero backend metrics without replacing them with defaults", async ({ page }) => {
    await openDashboard(page, { logs: [realLog], topology: healthyTopology });

    await expect(page.getByText("Logs Processed").locator("..").getByText("1", { exact: true })).toBeVisible();
    await expect(page.getByText("Current", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("api", { exact: true }).first()).toBeVisible();
  });

  test("does not show a healthy dashboard when every telemetry request fails", async ({ page }) => {
    await openDashboard(page, { logsStatus: 500, trackingStatus: 500, topologyStatus: 500, workerStatus: 500, socketMode: "disconnected" });

    await expect(page.getByText(/Dashboard telemetry is unavailable/)).toBeVisible();
    await expect(page.getByText("All reported healthy", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Healthy", { exact: true })).toHaveCount(0);
  });

  test("does not turn a timed-out metrics request into fabricated zeros", async ({ page }) => {
    await openDashboard(page, { logsTimeout: true, trackingStatus: 500, topologyStatus: 500, workerStatus: 500 });

    await expect(page.getByText(/Dashboard telemetry is unavailable/)).toBeVisible();
    await expect(page.getByText("Logs Processed").locator("..").getByText("0", { exact: true })).toHaveCount(0);
  });

  test("does not turn an anomaly API failure into zero anomalies", async ({ page }) => {
    await openDashboard(page, { logs: [realLog], trackingStatus: 500, topology: healthyTopology });
    await page.getByRole("link", { name: "Anomalies" }).click();

    await expect(page.getByRole("heading", { name: "Anomaly Detection" })).toBeVisible();
    await expect(page.getByText(/Anomaly data is unavailable/)).toBeVisible();
    await expect(page.getByText("No Anomaly Records", { exact: true })).toHaveCount(0);
  });

  test("exposes current and stale worker heartbeat state separately", async ({ page }) => {
    await openDashboard(page, { workers: staleWorkers, topology: healthyTopology });

    await expect(page.getByText("Worker heartbeats")).toBeVisible();
    await expect(page.getByText("stale", { exact: true })).toBeVisible();
    await expect(page.getByText("unavailable", { exact: true })).toBeVisible();
    await expect(page.getByText("Durable runtime evidence")).toBeVisible();
  });

  test("marks last-known telemetry stale after WebSocket disconnect", async ({ page }) => {
    await openDashboard(page, {
      logs: [realLog],
      topology: healthyTopology,
      socketMode: "disconnected",
      socketEvent: {
        type: "log.parsed",
        timestamp: SNAPSHOT_TIME,
        payload: { id: "01J8STREAMLOG00000000000000", service: "api", level: "INFO", template: "stream event", latency_ms: 10 },
      },
    });

    await expect(page.getByText("Telemetry disconnected", { exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("Stale", { exact: true })).not.toHaveCount(0);
    await expect(page.getByText("stream event", { exact: true })).toBeVisible();
  });

  test("keeps topology unavailable and empty states explicit", async ({ page }) => {
    await openDashboard(page, { trackingLoops: [anomalyLoop], topology: emptyTopology });
    await page.getByRole("link", { name: "Incidents" }).click();
    await expect(page.getByText("No topology was returned for this scope.")).toBeVisible();

    await page.unroute("**/api/v1/topology");
    await page.route("**/api/v1/topology", (route) => fulfillJson(route, 500, { detail: "synthetic test failure" }));
    await page.goto("/incidents");
    await expect(page.getByText(/Topology is unavailable/)).toBeVisible();
  });

  test("sanitizes sensitive callback tokens before navigation or refresh", async ({ page }) => {
    const token = "header.eyJleHAiOjQxMDAwMDAwMDB9.signature";
    await page.goto(`/login?token=${encodeURIComponent(token)}#oauth-callback`);
    await expect(page.getByText("Signed in successfully")).toBeVisible();
    await expect.poll(() => new URL(page.url()).search).toBe("");
    await expect.poll(() => new URL(page.url()).hash).toBe("");
    await page.reload();
    await expect(page.getByLabel("Email address")).toBeVisible();
    expect(page.url()).not.toContain("token=");
  });

  test("routes an expired session to login without retaining dashboard state", async ({ page }) => {
    await page.context().addCookies([
      { name: "logsentinel_csrf", value: "browser-test-csrf", url: "http://127.0.0.1:5173" },
    ]);
    await page.route("**/api/auth/refresh", (route) => fulfillJson(route, 401, { detail: "session_required" }));

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Observability Overview" })).toHaveCount(0);
  });

  test("represents reconnecting as a transport state instead of current telemetry", async ({ page }) => {
    await openDashboard(page, { logs: [realLog], topology: healthyTopology, socketMode: "reconnecting" });
    await expect(page.getByText("Telemetry disconnected", { exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("Healthy", { exact: true })).toHaveCount(0);
  });

  test("keeps an empty anomaly response distinct from anomaly request failure", async ({ page }) => {
    await openDashboard(page, { logs: [realZeroLog], trackingLoops: [], topology: healthyTopology });
    await page.getByRole("link", { name: "Anomalies" }).click();
    await expect(page.getByText("No Anomaly Records", { exact: true })).toBeVisible();
    await expect(page.getByText(/Anomaly data is unavailable/)).toHaveCount(0);
  });
});

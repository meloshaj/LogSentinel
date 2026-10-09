import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { Sidebar } from "../../src/layouts/Sidebar";
import { PRIMARY_NAV_ITEMS } from "../../src/constants/navigation";

const logoutMocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  clearMicrosoftAuthCache: vi.fn(),
  logoutSession: vi.fn(),
  useTelemetryStream: vi.fn(),
  useLiveLogs: vi.fn(),
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => logoutMocks.navigate };
});

vi.mock("../../src/providers/MsalProviderWrapper", () => ({
  clearMicrosoftAuthCache: logoutMocks.clearMicrosoftAuthCache,
}));

vi.mock("../../src/utils/auth", () => ({
  getAuthToken: () => null,
  logoutSession: logoutMocks.logoutSession,
}));

vi.mock("../../src/hooks/useTelemetryStream", () => ({
  useTelemetryStream: logoutMocks.useTelemetryStream,
}));

vi.mock("../../src/hooks/useLiveLogs", () => ({
  useLiveLogs: logoutMocks.useLiveLogs,
}));

const trackingLoop = (windowId: string, services: string[]) => ({
  id: Number(windowId.slice(-1)),
  window_id: windowId,
  anomaly_score: 0.9,
  severity: "critical",
  status: "open",
  suspected_root_service: services[0] ?? null,
  created_at: "2026-09-29T10:00:00.000Z",
  blast_radius: services.map((service_name, index) => ({
    service_name,
    impact_classification: index === 0 ? "root" as const : "direct" as const,
    dependency_path: [service_name],
    propagation_path: [service_name],
    impact_score: 0.9,
  })),
});

const performanceEvent = (metric_name: string) => ({
  metric_name,
  current_value: 10,
  threshold: 5,
  severity: "high",
});

function setTelemetry({
  status = "empty",
  loops = [],
  performanceEvents = [],
  connectionState = "live",
}: {
  status?: "loading" | "available" | "empty" | "stale" | "error";
  loops?: ReturnType<typeof trackingLoop>[];
  performanceEvents?: ReturnType<typeof performanceEvent>[];
  connectionState?: "live" | "reconnecting" | "failed";
} = {}) {
  const currentState = status === "loading"
    ? { status, data: null, lastUpdated: null, source: "test", error: null }
    : status === "error"
      ? { status, data: null, lastUpdated: null, source: "test", error: "offline" }
      : { status, data: loops, lastUpdated: new Date().toISOString(), source: "test", error: null };

  logoutMocks.useTelemetryStream.mockReturnValue({
    activeTrackingLoops: loops,
    latestPerformanceEvents: performanceEvents,
    trackingLoopsDataState: currentState,
  });
  logoutMocks.useLiveLogs.mockReturnValue({
    dataState: {
      status: "empty",
      data: [],
      lastUpdated: new Date().toISOString(),
      source: "test",
      error: null,
    },
    connectionState,
    filteredLogs: [],
  });
}

describe("Sidebar logout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    logoutMocks.clearMicrosoftAuthCache.mockResolvedValue(undefined);
    logoutMocks.logoutSession.mockResolvedValue(undefined);
    setTelemetry();
  });

  it("revokes the server session and clears MSAL before navigation", async () => {
    localStorage.setItem("authToken", "persistent-token");
    sessionStorage.setItem("authToken", "session-token");
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Log out of LogSentinel" }),
    );

    expect(logoutMocks.logoutSession).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(logoutMocks.clearMicrosoftAuthCache).toHaveBeenCalledTimes(1),
    );
    await waitFor(() =>
      expect(logoutMocks.navigate).toHaveBeenCalledWith("/login", {
        replace: true,
      }),
    );
    expect(
      logoutMocks.clearMicrosoftAuthCache.mock.invocationCallOrder[0],
    ).toBeLessThan(logoutMocks.navigate.mock.invocationCallOrder[0]);
  });

  it("shows no badges in the no-data state and navigation has no seeded counts", () => {
    setTelemetry({ status: "empty" });
    render(<MemoryRouter><Sidebar /></MemoryRouter>);

    expect(screen.getByRole("link", { name: "Anomalies" })).not.toHaveTextContent(/\d/);
    expect(screen.getByRole("link", { name: "Incidents" })).not.toHaveTextContent(/\d/);
    expect(PRIMARY_NAV_ITEMS.filter((item) => ["/anomalies", "/incidents"].includes(item.to)).every((item) => !("badge" in item))).toBe(true);
  });

  it("shows the current anomaly record count (deduplicated like the Anomalies page)", () => {
    setTelemetry({
      status: "available",
      loops: [trackingLoop("loop-1", ["api", "worker", "db"]), trackingLoop("loop-2", ["api", "queue"])],
    });
    render(<MemoryRouter><Sidebar /></MemoryRouter>);

    expect(screen.getByRole("link", { name: /Anomalies/ })).toHaveTextContent("4");
  });

  it("shows the current incident record count from loops and performance events", () => {
    setTelemetry({
      status: "available",
      loops: [trackingLoop("loop-1", ["api"])],
      performanceEvents: [performanceEvent("cpu_usage")],
    });
    render(<MemoryRouter><Sidebar /></MemoryRouter>);

    expect(screen.getByRole("link", { name: /Incidents/ })).toHaveTextContent("2");
  });

  it.each([
    ["null/loading count", { status: "loading" as const }],
    ["backend error", { status: "error" as const, loops: [trackingLoop("loop-1", ["api"])], connectionState: "failed" as const }],
    ["stale response", { status: "stale" as const, loops: [trackingLoop("loop-1", ["api"])], connectionState: "reconnecting" as const }],
  ])("hides both badges for %s", (_label, state) => {
    setTelemetry(state);
    render(<MemoryRouter><Sidebar /></MemoryRouter>);

    expect(screen.getByRole("link", { name: "Anomalies" })).not.toHaveTextContent(/\d/);
    expect(screen.getByRole("link", { name: "Incidents" })).not.toHaveTextContent(/\d/);
  });

  it("does not show zero-valued badges when current responses contain no records", () => {
    setTelemetry({ status: "available", loops: [], performanceEvents: [] });
    render(<MemoryRouter><Sidebar /></MemoryRouter>);

    expect(screen.getByRole("link", { name: "Anomalies" })).not.toHaveTextContent(/\d/);
    expect(screen.getByRole("link", { name: "Incidents" })).not.toHaveTextContent(/\d/);
  });

  it("caps large real counts at 99+", () => {
    const manyServices = Array.from({ length: 100 }, (_, index) => `service-${index}`);
    setTelemetry({ status: "available", loops: [trackingLoop("loop-1", manyServices)] });
    render(<MemoryRouter><Sidebar /></MemoryRouter>);

    expect(screen.getByRole("link", { name: /Anomalies/ })).toHaveTextContent("99+");
  });
});

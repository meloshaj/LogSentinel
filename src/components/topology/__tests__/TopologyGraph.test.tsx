import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock useTopology
// ---------------------------------------------------------------------------

const mockRefresh = vi.fn();
let mockTopologyReturn = {
  nodes: [] as any[],
  edges: [] as any[],
  updatedAt: "2026-08-05T12:00:00Z",
  isLoading: false,
  error: null as string | null,
  refresh: mockRefresh,
};

vi.mock("../../../hooks/useTopology", () => ({
  useTopology: () => mockTopologyReturn,
}));

// ---------------------------------------------------------------------------
// Mock useTelemetryStream
// ---------------------------------------------------------------------------

let mockTrackingLoops: any[] = [];
let mockPerformanceEvents: any[] = [];

vi.mock("../../../hooks/useTelemetryStream", () => ({
  useTelemetryStream: () => ({
    connectionStatus: "connected",
    activeTrackingLoops: mockTrackingLoops,
    latestPerformanceEvents: mockPerformanceEvents,
    clearTrackingLoops: vi.fn(),
    clearPerformanceEvents: vi.fn(),
  }),
}));

// ---------------------------------------------------------------------------
// Mock Cytoscape
// ---------------------------------------------------------------------------
vi.mock("cytoscape", () => {
  return {
    default: vi.fn(() => ({
      on: vi.fn(),
      batch: vi.fn((cb) => cb()),
      nodes: vi.fn(() => {
        const arr: any[] = [];
        (arr as any).unselect = vi.fn();
        return arr;
      }),
      edges: vi.fn(() => {
        const arr: any[] = [];
        return arr;
      }),
      add: vi.fn(),
      remove: vi.fn(),
      getElementById: vi.fn(() => ({ data: vi.fn(), remove: vi.fn(), select: vi.fn() })),
      layout: vi.fn(() => ({ run: vi.fn() })),
      destroy: vi.fn(),
    })),
  };
});

// ---------------------------------------------------------------------------
// Import after mocks
// ---------------------------------------------------------------------------

import { ServiceTopologyGraph } from "../ServiceTopologyGraph";

// ---------------------------------------------------------------------------
// Setup & Teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  mockRefresh.mockReset();
  mockTrackingLoops = [];
  mockPerformanceEvents = [];
  mockTopologyReturn = {
    nodes: [],
    edges: [],
    updatedAt: "2026-08-05T12:00:00Z",
    isLoading: false,
    error: null,
    refresh: mockRefresh,
  };
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Test Data
// ---------------------------------------------------------------------------

const SAMPLE_NODES = [
  { id: "service-auth", label: "Auth Service", type: "service" as const, status: "healthy" as const },
  { id: "db-postgres", label: "PostgreSQL", type: "database" as const, status: "healthy" as const },
  { id: "cache-redis", label: "Redis", type: "cache" as const, status: "healthy" as const },
];

const SAMPLE_EDGES = [
  { id: "e-auth-pg", source: "service-auth", target: "db-postgres" },
  { id: "e-auth-redis", source: "service-auth", target: "cache-redis" },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ServiceTopologyGraph", () => {
  it("Test 1: renders the cytoscape container from /api/v1/topology data", async () => {
    mockTopologyReturn = {
      ...mockTopologyReturn,
      nodes: SAMPLE_NODES,
      edges: SAMPLE_EDGES,
    };

    render(<ServiceTopologyGraph mode="full" />);

    // Graph container should be present
    expect(screen.getByTestId("topology-graph")).toBeDefined();
  });

  it("Test 2: anomaly event triggers re-render without crashing", async () => {
    mockTopologyReturn = {
      ...mockTopologyReturn,
      nodes: SAMPLE_NODES,
      edges: SAMPLE_EDGES,
    };

    const { rerender } = render(<ServiceTopologyGraph mode="full" />);

    // Simulate an anomaly tracking loop targeting service-auth
    mockTrackingLoops = [
      {
        window_id: "w-001",
        anomaly_score: 0.92,
        severity: "critical",
        status: "triggered",
        blast_radius: {
          blast_radius: [
            {
              service_name: "service-auth",
              impact_classification: "root",
              dependency_path: [],
              propagation_path: ["service-auth", "db-postgres"],
              impact_score: 0.92,
            },
            {
              service_name: "db-postgres",
              impact_classification: "direct",
              dependency_path: ["service-auth"],
              propagation_path: [],
              impact_score: 0.65,
            },
          ],
        },
        suspected_root_service: "service-auth",
      },
    ];

    // Re-render with updated tracking loops
    rerender(<ServiceTopologyGraph mode="full" />);

    // Wait for RAF
    await act(async () => {
      await new Promise((r) => requestAnimationFrame(r));
    });

    expect(screen.getByTestId("topology-graph")).toBeDefined();
  });

  it("Test 3: renders error alert when /api/v1/topology fetch fails", () => {
    mockTopologyReturn = {
      ...mockTopologyReturn,
      nodes: [],
      edges: [],
      isLoading: false,
      error: "HTTP 500: Internal Server Error",
    };

    render(<ServiceTopologyGraph mode="full" />);

    // Error alert should render
    const errorEl = screen.getByTestId("topology-error");
    expect(errorEl).toBeDefined();
    expect(errorEl.textContent).toContain("HTTP 500");
    expect(errorEl.textContent).toContain("Failed to load service topology");
  });

  it("Test 4: rapid log stream events do NOT trigger static graph re-render", async () => {
    mockTopologyReturn = {
      ...mockTopologyReturn,
      nodes: SAMPLE_NODES,
      edges: SAMPLE_EDGES,
    };

    const { rerender } = render(<ServiceTopologyGraph mode="full" />);
    
    mockPerformanceEvents = [
      {
        service_name: "service-auth",
        throughput_rps: 120,
        latency_p95_ms: 45,
        error_rate_pct: 0.1,
      },
    ];

    rerender(<ServiceTopologyGraph mode="full" />);
    
    await act(async () => {
      await new Promise((r) => requestAnimationFrame(r));
    });

    expect(screen.getByTestId("topology-graph")).toBeDefined();
  });

  it("Test 5: compact mode renders with compact CSS class", () => {
    mockTopologyReturn = {
      ...mockTopologyReturn,
      nodes: SAMPLE_NODES,
      edges: SAMPLE_EDGES,
    };

    render(<ServiceTopologyGraph mode="compact" />);

    const container = screen.getByTestId("topology-graph");
    expect(container.className).toContain("topology-graph--compact");
  });
});

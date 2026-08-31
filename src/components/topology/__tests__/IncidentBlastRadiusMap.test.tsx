import { render, screen } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";

let topology = { nodes: [] as any[], edges: [] as any[] };

vi.mock("../../../hooks/useTopology", () => ({
  useTopology: () => ({
    ...topology,
    isLoading: false,
    error: null,
    updatedAt: null,
    refresh: vi.fn(),
  }),
}));

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    Handle: () => null,
    Background: () => null,
    Controls: () => null,
    BaseEdge: () => null,
    ReactFlow: ({ nodes }: { nodes: any[] }) => (
      <div data-testid="radar">
        {nodes.map((node) => (
          <span key={node.id}>{node.data.node.name || node.data.node.id}</span>
        ))}
      </div>
    ),
    useReactFlow: () => ({ fitView: vi.fn() }),
  };
});

import { IncidentBlastRadiusMap, RadarNode } from "../IncidentBlastRadiusMap";

beforeEach(() => {
  topology = { nodes: [], edges: [] };
});

describe("IncidentBlastRadiusMap resilience", () => {
  it("renders a node with missing metrics, icon type, and name without crashing", () => {
    render(
      <RadarNode
        {...({
          id: "service-a",
          data: {
            node: {
              id: "service-a",
              name: "",
              type: "unknown" as any,
              status: "degraded",
              metrics: undefined,
            },
            status: "affected",
            onSelect: vi.fn(),
          },
        } as any)}
      />,
    );

    expect(screen.getByText("service-a")).toBeInTheDocument();
    expect(screen.getAllByText("unavailable")).toHaveLength(2);
  });

  it("does not invent an orphan node when graph root is unavailable", async () => {
    topology = {
      nodes: [
        {
          id: "order-service",
          name: "order-service",
          type: "service",
          status: "critical",
          metrics: null,
        },
      ],
      edges: [],
    };

    render(
      <IncidentBlastRadiusMap
        rootCause="missing-root"
        affectedServices={["order-service"]}
        onSelect={vi.fn()}
      />,
    );

    expect(await screen.findByTestId("radar")).toHaveTextContent("order-service");
    expect(screen.queryByText("missing-root")).not.toBeInTheDocument();
  });
});

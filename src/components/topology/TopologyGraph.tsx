/**
 * TopologyGraph — Interactive SVG service topology with performance-isolated
 * anomaly overlays.
 *
 * ## Performance Architecture
 *
 * The component is split into two rendering tiers:
 *
 * 1. **Static Graph Layer** (`StaticGraph`, wrapped in `React.memo`):
 *    Re-renders ONLY when `useTopology()` returns new node/edge arrays
 *    (every ~30 s poll). Log stream events never touch this layer.
 *
 * 2. **Overlay Layer** (imperative DOM mutations via `useRef`):
 *    Reads `activeTrackingLoops` from `useTelemetryStream()` and mutates
 *    CSS classes directly on SVG elements via `getElementById()`.
 *    Gated to ≤30 FPS through `requestAnimationFrame`.
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTopology } from "../../hooks/useTopology";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";
import type { TopologyNode as TNode, TopologyEdge as TEdge } from "../../types/topology";
import type { TrackingLoopEvent } from "../../providers/TelemetryProvider";
import { TopologyNode } from "./TopologyNode";
import { TopologyEdge } from "./TopologyEdge";
import "./topology.css";

import {
  AlertTriangle,
  RefreshCw,
  Loader2,
  Network,
  Shield,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Layout helpers — simple force-free radial layout
// ---------------------------------------------------------------------------

interface PositionedNode {
  node: TNode;
  x: number;
  y: number;
}

function layoutNodes(
  nodes: TNode[],
  width: number,
  height: number,
): PositionedNode[] {
  if (nodes.length === 0) return [];
  if (nodes.length === 1) {
    return [{ node: nodes[0], x: width / 2, y: height / 2 }];
  }

  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.35;

  return nodes.map((node, i) => {
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    return {
      node,
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    };
  });
}

// ---------------------------------------------------------------------------
// Static graph layer (memoised — never re-renders from log events)
// ---------------------------------------------------------------------------

interface StaticGraphProps {
  nodes: PositionedNode[];
  edges: TEdge[];
  posMap: Map<string, { x: number; y: number }>;
  selectedNodeId: string | null;
  onNodeClick: (id: string) => void;
  /** Incremented whenever useTopology returns new data */
  revision: number;
}

const StaticGraph = React.memo<StaticGraphProps>(
  ({ nodes, edges, posMap, selectedNodeId, onNodeClick }) => (
    <>
      {/* Edges first (behind nodes) */}
      {edges.map((edge) => {
        const src = posMap.get(edge.source);
        const tgt = posMap.get(edge.target);
        if (!src || !tgt) return null;
        return (
          <TopologyEdge
            key={edge.id}
            edge={edge}
            x1={src.x}
            y1={src.y}
            x2={tgt.x}
            y2={tgt.y}
          />
        );
      })}
      {/* Nodes */}
      {nodes.map((pn) => (
        <TopologyNode
          key={pn.node.id}
          node={pn.node}
          x={pn.x}
          y={pn.y}
          selected={pn.node.id === selectedNodeId}
          onClick={onNodeClick}
        />
      ))}
    </>
  ),
  (prev, next) => prev.revision === next.revision && prev.selectedNodeId === next.selectedNodeId,
);

StaticGraph.displayName = "StaticGraph";

// ---------------------------------------------------------------------------
// Main TopologyGraph component
// ---------------------------------------------------------------------------

export interface TopologyGraphProps {
  /** "compact" for OverviewPage widget, "full" for IncidentsPage interactive. */
  mode?: "compact" | "full";
  /** Called when user clicks a node (full mode). */
  onNodeSelect?: (nodeId: string | null) => void;
  /** Externally controlled selected node. */
  selectedNodeId?: string | null;
}

export function TopologyGraph({
  mode = "full",
  onNodeSelect,
  selectedNodeId: controlledSelectedId,
}: TopologyGraphProps) {
  const { nodes, edges, updatedAt, isLoading, error, refresh } = useTopology();
  const { activeTrackingLoops } = useTelemetryStream();

  const [internalSelectedId, setInternalSelectedId] = useState<string | null>(null);
  const selectedNodeId = controlledSelectedId ?? internalSelectedId;

  const containerRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number | null>(null);
  const lastOverlayRef = useRef<string>(""); // serialised overlay state for dirty check

  // Track topology data revision for StaticGraph memo
  const revisionRef = useRef(0);
  const prevNodesRef = useRef(nodes);
  if (nodes !== prevNodesRef.current) {
    revisionRef.current += 1;
    prevNodesRef.current = nodes;
  }

  // Dimensions
  const width = mode === "compact" ? 400 : 800;
  const height = mode === "compact" ? 280 : 480;

  // Layout
  const positionedNodes = useMemo(
    () => layoutNodes(nodes, width, height),
    [nodes, width, height],
  );

  const posMap = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>();
    for (const pn of positionedNodes) {
      m.set(pn.node.id, { x: pn.x, y: pn.y });
    }
    return m;
  }, [positionedNodes]);

  // Node click handler
  const handleNodeClick = useCallback(
    (nodeId: string) => {
      const next = nodeId === selectedNodeId ? null : nodeId;
      setInternalSelectedId(next);
      onNodeSelect?.(next);
    },
    [selectedNodeId, onNodeSelect],
  );

  // ---- Imperative overlay layer (30 FPS gated) ----
  useEffect(() => {
    if (!containerRef.current) return;

    const applyOverlays = () => {
      const container = containerRef.current;
      if (!container) return;

      // Build a map of nodeId → overlay status from tracking loops
      const overlayMap = new Map<
        string,
        { status: "degraded" | "critical"; isRoot: boolean }
      >();

      for (const loop of activeTrackingLoops) {
        const blastData = loop.blast_radius as any;
        if (blastData?.blast_radius && Array.isArray(blastData.blast_radius)) {
          for (const brNode of blastData.blast_radius) {
            if (typeof brNode.service_name !== "string") continue;
            const existing = overlayMap.get(brNode.service_name);
            const isRoot = brNode.impact_classification === "root";
            const severity =
              loop.severity === "critical" || loop.severity === "high"
                ? "critical"
                : "degraded";

            if (
              !existing ||
              severity === "critical" ||
              (severity === "degraded" && existing.status !== "critical")
            ) {
              overlayMap.set(brNode.service_name, {
                status: severity as "degraded" | "critical",
                isRoot: isRoot || (existing?.isRoot ?? false),
              });
            }
          }
        }
      }

      // Dirty check — skip DOM mutations if nothing changed
      const serialised = JSON.stringify(
        Array.from(overlayMap.entries()).sort(),
      );
      if (serialised === lastOverlayRef.current) return;
      lastOverlayRef.current = serialised;

      // Apply CSS class mutations to SVG elements
      const nodeGroups = container.querySelectorAll<SVGGElement>(
        "[data-node-id]",
      );
      for (const g of nodeGroups) {
        const nodeId = g.getAttribute("data-node-id");
        if (!nodeId) continue;

        const overlay = overlayMap.get(nodeId);

        // Remove existing overlay classes
        g.classList.remove(
          "topo-node--degraded",
          "topo-node--critical",
          "topo-node--healthy",
        );

        if (overlay) {
          g.classList.add(`topo-node--${overlay.status}`);

          // Blast radius ring
          const ring = g.querySelector("[data-blast-ring]") as SVGCircleElement | null;
          if (ring) {
            ring.style.opacity = overlay.isRoot ? "1" : "0";
          }
        } else {
          g.classList.add("topo-node--healthy");
          const ring = g.querySelector("[data-blast-ring]") as SVGCircleElement | null;
          if (ring) ring.style.opacity = "0";
        }
      }
    };

    // RAF-gated loop
    let running = true;
    const tick = () => {
      if (!running) return;
      applyOverlays();
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      running = false;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [activeTrackingLoops]);

  // ---- Blast radius count for compact badge ----
  const blastRadiusCount = useMemo(() => {
    let count = 0;
    for (const loop of activeTrackingLoops) {
      const blastData = loop.blast_radius as any;
      if (blastData?.blast_radius && Array.isArray(blastData.blast_radius)) {
        count += blastData.blast_radius.length;
      }
    }
    return count;
  }, [activeTrackingLoops]);

  // ---- Render ----

  if (error && nodes.length === 0) {
    return (
      <div
        className="topology-graph-container flex items-center justify-center"
        style={{ height }}
        data-testid="topology-error"
      >
        <div className="topology-error-alert">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span>Failed to load topology: {error}</span>
          <button
            onClick={refresh}
            className="ml-2 px-2 py-1 rounded bg-[#21262d] text-[#e6edf3] hover:bg-[#30363d] transition-colors"
            style={{ fontSize: "11px" }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={`topology-graph-container ${mode === "compact" ? "topology-graph--compact" : ""}`}
      style={{ height, width: "100%" }}
      data-testid="topology-graph"
    >
      {/* Header bar */}
      <div className="absolute top-3 left-4 right-4 z-10 flex items-center justify-between pointer-events-none">
        <div className="flex items-center gap-2 pointer-events-auto">
          <Network className="w-4 h-4 text-[#388bfd]" />
          <span
            className="text-[#e6edf3]"
            style={{ fontSize: "13px", fontWeight: 600 }}
          >
            {mode === "compact" ? "System Health" : "Service Topology"}
          </span>
          {blastRadiusCount > 0 && (
            <span
              className="px-1.5 py-0.5 rounded-full text-white"
              style={{
                fontSize: "9px",
                fontWeight: 700,
                background: "#da3633",
              }}
            >
              {blastRadiusCount} affected
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 pointer-events-auto">
          {updatedAt && (
            <span className="text-[#484f58]" style={{ fontSize: "10px" }}>
              {new Date(updatedAt).toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={refresh}
            disabled={isLoading}
            className="p-1 rounded hover:bg-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors disabled:opacity-40"
            title="Refresh topology"
          >
            {isLoading ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <RefreshCw className="w-3.5 h-3.5" />
            )}
          </button>
        </div>
      </div>

      {/* SVG graph */}
      {nodes.length === 0 && !isLoading ? (
        <div className="flex flex-col items-center justify-center h-full text-[#484f58] gap-2">
          <Shield className="w-8 h-8" />
          <span style={{ fontSize: "12px" }}>
            No topology data available yet
          </span>
        </div>
      ) : (
        <svg
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="xMidYMid meet"
          data-testid="topology-svg"
        >
          <StaticGraph
            nodes={positionedNodes}
            edges={edges}
            posMap={posMap}
            selectedNodeId={selectedNodeId}
            onNodeClick={handleNodeClick}
            revision={revisionRef.current}
          />
        </svg>
      )}
    </div>
  );
}

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { 
  ReactFlow, 
  Controls, 
  Background, 
  Panel,
  useNodesState, 
  useEdgesState, 
  MarkerType,
  Position
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { useTopology } from "../../hooks/useTopology";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";
import { TopologyNode } from "./TopologyNode";
import { TopologyEdge } from "./TopologyEdge";
import { AlertTriangle, RefreshCw, Loader2, Network, Shield } from "lucide-react";
import "./topology.css";

const nodeTypes = {
  custom: TopologyNode,
};

const edgeTypes = {
  custom: TopologyEdge,
};

const getLayoutedElements = (nodes: any[], edges: any[], direction = 'LR') => {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  
  const isHorizontal = direction === 'LR';
  dagreGraph.setGraph({ rankdir: direction, nodesep: 100, ranksep: 180 });

  nodes.forEach((node) => {
    // Approx dimensions for our custom nodes
    dagreGraph.setNode(node.id, { width: 180, height: 60 });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  const newNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    const newNode = {
      ...node,
      targetPosition: isHorizontal ? Position.Left : Position.Top,
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
      // We are shifting the dagre node position (anchor=center center) to the top left
      // so it matches the React Flow node anchor point (top left).
      position: {
        x: nodeWithPosition.x - 180 / 2,
        y: nodeWithPosition.y - 60 / 2,
      },
    };
    return newNode;
  });

  return { nodes: newNodes, edges };
};

export interface TopologyGraphProps {
  mode?: "compact" | "full" | "root-cause-focus";
  onNodeSelect?: (nodeId: string | null) => void;
  selectedNodeId?: string | null;
  showLowSeverity?: boolean;
}

export function ServiceTopologyGraph({ mode = "full", onNodeSelect, selectedNodeId: controlledSelectedId, showLowSeverity = true }: TopologyGraphProps) {
  const { nodes: initialNodes, edges: initialEdges, updatedAt, isLoading, error, refresh } = useTopology();
  const { activeTrackingLoops } = useTelemetryStream();

  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);

  const [internalSelectedId, setInternalSelectedId] = useState<string | null>(null);
  const selectedNodeId = controlledSelectedId ?? internalSelectedId;

  // Track active anomalies to build the overlay map
  const overlayMap = useMemo(() => {
    const map = new Map<string, { status: string; isRoot: boolean }>();
    
    for (const loop of activeTrackingLoops) {
      if (!showLowSeverity && loop.severity === "low") continue;
      
      const blastData = loop.blast_radius as any;
      if (blastData?.blast_radius && Array.isArray(blastData.blast_radius)) {
        for (const brNode of blastData.blast_radius) {
          if (typeof brNode.service_name !== "string") continue;
          
          const existing = map.get(brNode.service_name);
          const isRoot = brNode.impact_classification === "root";
          const severity = loop.severity === "critical" || loop.severity === "high" ? "critical" : "degraded";

          if (!existing || severity === "critical" || (severity === "degraded" && existing.status !== "critical")) {
            map.set(brNode.service_name, {
              status: severity,
              isRoot: isRoot || (existing?.isRoot ?? false),
            });
          }
        }
      }
    }
    return map;
  }, [activeTrackingLoops, showLowSeverity]);

  // Sync initial graph data with React Flow state and compute layout
  useEffect(() => {
    if (initialNodes.length === 0) return;

    // Build React Flow nodes
    const rfNodes = initialNodes.map(node => {
      const overlay = overlayMap.get(node.id);
      return {
        id: node.id,
        type: 'custom',
        data: {
          node,
          status: overlay ? overlay.status : 'healthy',
          isRoot: overlay?.isRoot || false,
          isPath: mode === "root-cause-focus" && overlay !== undefined,
          onNodeClick: (id: string) => {
            const next = id === selectedNodeId ? null : id;
            setInternalSelectedId(next);
            onNodeSelect?.(next);
          }
        },
        position: { x: 0, y: 0 },
        targetPosition: Position.Left,
        sourcePosition: Position.Right,
      };
    });

    // Build React Flow edges
    const rfEdges = initialEdges.map(edge => {
      const srcOverlay = overlayMap.get(edge.source);
      const tgtOverlay = overlayMap.get(edge.target);
      
      // If either end is critical, edge is critical. If degraded, edge is degraded.
      let status = 'healthy';
      if (srcOverlay?.status === 'critical' || tgtOverlay?.status === 'critical') status = 'critical';
      else if (srcOverlay?.status === 'degraded' || tgtOverlay?.status === 'degraded') status = 'degraded';
      
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: 'custom',
        animated: true,
        data: {
          latency_ms: edge.latency_ms,
          status
        }
      };
    });

    // Apply layout
    const layouted = getLayoutedElements(rfNodes, rfEdges, 'LR');
    setNodes(layouted.nodes);
    setEdges(layouted.edges);
    
  }, [initialNodes, initialEdges, overlayMap, selectedNodeId, onNodeSelect, setNodes, setEdges]);
  
  // Selection effect
  useEffect(() => {
    setNodes(nds => nds.map((n: any) => ({ ...n, selected: n.id === selectedNodeId })));
  }, [selectedNodeId, setNodes]);

  const blastRadiusCount = useMemo(() => {
    let count = 0;
    for (const loop of activeTrackingLoops) {
      if (!showLowSeverity && loop.severity === "low") continue;
      const blastData = loop.blast_radius as any;
      if (blastData?.blast_radius && Array.isArray(blastData.blast_radius)) {
        count += blastData.blast_radius.length;
      }
    }
    return count;
  }, [activeTrackingLoops, showLowSeverity]);

  if (error && initialNodes.length === 0) {
    return (
      <div className="topology-graph-container flex items-center justify-center w-full h-full bg-[#161b22] rounded-xl border border-[#21262d]">
        <div className="flex items-center gap-2 text-[#f85149]">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span>Failed to load topology: {error}</span>
          <button onClick={refresh} className="ml-2 px-2 py-1 rounded bg-[#21262d] text-[#e6edf3] hover:bg-[#30363d] transition-colors" style={{ fontSize: "11px" }}>Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative w-full h-full bg-[#0d1117] rounded-xl border border-[#21262d] overflow-hidden group">
      {/* Header bar */}
      <div className="absolute top-3 left-4 right-4 z-10 flex items-center justify-between pointer-events-none">
        <div className="flex items-center gap-2 pointer-events-auto bg-[#161b22]/80 backdrop-blur-sm px-3 py-1.5 rounded-lg border border-[#21262d]">
          <Network className="w-4 h-4 text-[#388bfd]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>
            {mode === "compact" ? "System Health" : "Service Topology"}
          </span>
          {blastRadiusCount > 0 && (
            <span className="px-1.5 py-0.5 rounded-full text-white" style={{ fontSize: "9px", fontWeight: 700, background: "#da3633" }}>
              {blastRadiusCount} affected
            </span>
          )}
        </div>
      </div>
      
      {initialNodes.length === 0 && isLoading ? (
        <div className="absolute inset-0 flex items-center justify-center bg-[#0d1117] z-10">
          <div className="flex flex-col items-center gap-4">
            <Loader2 className="w-8 h-8 text-[#388bfd] animate-spin" />
            <span className="text-[#e6edf3] font-semibold text-sm">Building Topology Graph...</span>
            <div className="w-48 h-1 bg-[#21262d] rounded-full overflow-hidden">
               <div className="h-full bg-[#388bfd] animate-pulse rounded-full w-2/3" />
            </div>
          </div>
        </div>
      ) : initialNodes.length === 0 && !isLoading ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-[#484f58] gap-2 z-10">
          <Shield className="w-8 h-8" />
          <span style={{ fontSize: "12px" }}>No topology data available yet</span>
        </div>
      ) : (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.1}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
          className="bg-[#0d1117]"
        >
          <Background color="#21262d" gap={16} size={1} />
          
          <Panel position="top-right" className="bg-[#161b22]/80 backdrop-blur-sm rounded-lg border border-[#21262d] flex items-center p-1 pointer-events-auto">
            {updatedAt && (
              <span className="text-[#484f58] px-2" style={{ fontSize: "10px" }}>
                {new Date(updatedAt).toLocaleTimeString()}
              </span>
            )}
            <button onClick={refresh} disabled={isLoading} className="p-1.5 rounded hover:bg-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors disabled:opacity-40" title="Refresh topology">
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            </button>
          </Panel>
          
          <Controls 
            className="bg-[#161b22] border-[#21262d] fill-[#7d8590] [&>button]:border-b-[#21262d] [&>button:hover]:bg-[#21262d] [&>button]:transition-colors"
          />
        </ReactFlow>
      )}
    </div>
  );
}

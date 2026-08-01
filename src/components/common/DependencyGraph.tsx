import { useMemo } from "react";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import { DEPENDENCY_NODE_STATUS } from "../../constants/statusConfig";
import { reactFlowEngineConfig } from "../../config/reactFlow";
import type { ServiceGraph } from "../../types/monitoring";

interface DependencyGraphProps {
  graph: ServiceGraph;
  glowRadius?: number;
  nodeRadius?: number;
  strokeWidth?: number;
  labelOffset?: number;
}

type ServiceNodeData = {
  label: string;
  status: {
    fill: string;
    stroke: string;
  };
  radius: number;
  labelOffset: number;
  glowRadius: number;
  strokeWidth: number;
};

function ServiceNode({ data }: NodeProps<Node<ServiceNodeData>>) {
  return (
    <div
      className="flex flex-col items-center justify-start"
      style={{
        width: 76,
        height: 60,
        color: "#c9d1d9",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0, width: 10, height: 10 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0, width: 10, height: 10 }} />
      <div
        className="rounded-full"
        style={{
          width: data.radius * 2,
          height: data.radius * 2,
          marginTop: 1,
          border: `${data.strokeWidth}px solid ${data.status.stroke}`,
          background: data.status.fill,
          boxShadow: data.label === "database-service" || data.label === "payment-service" ? `0 0 0 ${data.glowRadius}px rgba(218,54,51,0.12)` : "none",
        }}
      />
      <div
        className="mt-2 text-center leading-tight"
        style={{
          fontSize: 9,
          fontWeight: 500,
          color: "#c9d1d9",
          marginTop: data.labelOffset - data.radius * 2,
        }}
      >
        {data.label.replace("-service", "")}
      </div>
    </div>
  );
}

export function DependencyGraph({
  graph,
  glowRadius = 18,
  nodeRadius = 13,
  strokeWidth = 1.5,
  labelOffset = 26,
}: DependencyGraphProps) {
  const { nodes, edges, nodeTypes } = useMemo(() => {
    const mappedNodes: Node<ServiceNodeData>[] = graph.nodes.map((node) => {
      const status = DEPENDENCY_NODE_STATUS[node.id] ?? { fill: "#1c2128", stroke: "#484f58" };
      const isCritical = node.id === "database-service" || node.id === "payment-service";

      return {
        id: node.id,
        type: "serviceNode",
        position: { x: node.x, y: node.y },
        data: {
          label: node.id,
          status,
          radius: nodeRadius,
          labelOffset,
          glowRadius,
          strokeWidth,
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: false,
        selectable: false,
        style: {
          width: 76,
          height: 60,
          background: "transparent",
          border: "none",
          boxShadow: "none",
          opacity: isCritical ? 1 : 0.98,
        },
      };
    });

    const mappedEdges: Edge[] = graph.edges.map((edge) => {
      const isCritical = edge.to === "database-service" || edge.from === "database-service";

      return {
        id: `${edge.from}-${edge.to}`,
        source: edge.from,
        target: edge.to,
        type: "smoothstep",
        animated: isCritical,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color: isCritical ? "rgba(248,81,73,0.65)" : "rgba(72,79,88,0.8)",
        },
        style: {
          stroke: isCritical ? "rgba(248,81,73,0.65)" : "rgba(72,79,88,0.8)",
          strokeWidth: isCritical ? 1.5 : 1,
        },
      };
    });

    return { nodes: mappedNodes, edges: mappedEdges, nodeTypes: { serviceNode: ServiceNode } };
  }, [glowRadius, graph.edges, graph.nodes, strokeWidth]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      defaultViewport={reactFlowEngineConfig.defaultViewport}
      fitView
      fitViewOptions={reactFlowEngineConfig.fitViewOptions}
      nodesDraggable={reactFlowEngineConfig.nodesDraggable}
      nodesConnectable={reactFlowEngineConfig.nodesConnectable}
      elementsSelectable={reactFlowEngineConfig.elementsSelectable}
      elevateNodesOnSelect={reactFlowEngineConfig.elevateNodesOnSelect}
      panOnDrag={reactFlowEngineConfig.panOnDrag}
      panOnScroll={reactFlowEngineConfig.panOnScroll}
      zoomOnScroll={reactFlowEngineConfig.zoomOnScroll}
      zoomOnPinch={reactFlowEngineConfig.zoomOnPinch}
      autoPanOnNodeDrag={reactFlowEngineConfig.autoPanOnNodeDrag}
      snapToGrid={reactFlowEngineConfig.snapToGrid}
      snapGrid={[
        reactFlowEngineConfig.snapGrid[0],
        reactFlowEngineConfig.snapGrid[1],
      ]}
      proOptions={{ hideAttribution: true }}
      nodesFocusable={false}
      edgesFocusable={false}
      className="w-full h-full"
    >
      <Background color="rgba(72,79,88,0.25)" gap={18} size={1} />
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
  );
}

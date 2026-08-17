import { useEffect, useMemo } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  getBezierPath,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { AlertTriangle, Database, Radio, Server, Share2, Zap } from "lucide-react";
import { useTopology } from "../../hooks/useTopology";
import type { NodeType, TopologyNode } from "../../types/topology";
import "@xyflow/react/dist/style.css";

type RadarStatus = "root" | "affected" | "nominal";

type RadarNodeData = {
  node: TopologyNode;
  status: RadarStatus;
  onSelect: (id: string) => void;
};

type RadarEdgeData = { isCascade: boolean };

const nodeIcons: Record<NodeType, typeof Server> = {
  gateway: Share2,
  service: Server,
  database: Database,
  cache: Zap,
  queue: Radio,
};

function RadarNode({ data }: NodeProps<Node<RadarNodeData>>) {
  const Icon = nodeIcons[data.node.type];
  const root = data.status === "root";
  const affected = data.status === "affected";
  const accent = root ? "#EF4444" : affected ? "#F59E0B" : "#334155";
  const latency = data.node.metrics?.latency_ms ?? (root ? 5120 : affected ? 860 : 24);
  const errorRate = data.node.metrics?.error_rate ?? (root ? 8.4 : affected ? 2.1 : 0);

  return (
    <div className="relative flex h-[138px] w-[138px] items-center justify-center">
      <Handle type="target" position={Position.Left} className="!opacity-0" />
      {root && <span className="absolute inset-0 rounded-full border border-[#EF4444]/70 animate-ping" />}
      <button
        type="button"
        onClick={() => data.onSelect(data.node.id)}
        title={`Inspect ${data.node.label}`}
        className="relative flex h-[118px] w-[118px] flex-col items-center justify-center rounded-full border bg-[#111827] px-2 text-center shadow-lg transition-transform hover:scale-105 focus:outline-none focus:ring-2 focus:ring-[#388bfd]"
        style={{ borderColor: accent, boxShadow: root ? "0 0 28px rgba(239,68,68,0.7)" : affected ? "0 0 18px rgba(245,158,11,0.45)" : "none" }}
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-full" style={{ backgroundColor: `${accent}22`, color: accent }}>
          {root ? <AlertTriangle className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
        </span>
        <span className="mt-1 max-w-[100px] truncate font-mono text-[10px] font-bold text-[#e6edf3]">{data.node.label}</span>
        <span className="mt-0.5 text-[8px] font-bold uppercase tracking-wide" style={{ color: accent }}>
          {root ? "Root cause" : affected ? "Cascade" : "Nominal"}
        </span>
        <span className="mt-1 flex items-center gap-1.5 text-[8px] font-mono text-[#94a3b8]">
          <span>{latency >= 1000 ? `${(latency / 1000).toFixed(1)}s` : `${latency}ms`}</span>
          <span className="h-1 w-1 rounded-full bg-[#475569]" />
          <span>{errorRate.toFixed(1)}% err</span>
        </span>
        <span className="mt-0.5 text-[7px] uppercase tracking-wide text-[#64748b]">{data.node.type}</span>
      </button>
      <Handle type="source" position={Position.Right} className="!opacity-0" />
    </div>
  );
}

function CascadeEdge(props: EdgeProps<Edge<RadarEdgeData>>) {
  const [path] = getBezierPath({ ...props, curvature: 0.32 });
  const cascade = props.data?.isCascade ?? false;
  const stroke = cascade ? "#F59E0B" : "#475569";

  return (
    <>
      <BaseEdge path={path} markerEnd={props.markerEnd} style={{ stroke, strokeWidth: cascade ? 2.2 : 1.25, strokeDasharray: cascade ? "7 5" : undefined }} />
      {cascade && (
        <circle r="3" fill="#EF4444">
          <animateMotion dur="1.2s" repeatCount="indefinite" path={path} />
        </circle>
      )}
    </>
  );
}

const nodeTypes = { radar: RadarNode };
const edgeTypes = { cascade: CascadeEdge };

function AutoCenter({ rootId }: { rootId: string }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const frame = requestAnimationFrame(() => fitView({ nodes: [{ id: rootId }], padding: 0.7, duration: 450, maxZoom: 1 }));
    return () => cancelAnimationFrame(frame);
  }, [fitView, rootId]);
  return null;
}

export function IncidentBlastRadiusMap({ rootCause, affectedServices, onSelect }: { rootCause: string; affectedServices: string[]; onSelect: (id: string) => void }) {
  const { nodes: topologyNodes, edges: topologyEdges } = useTopology();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<RadarNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<RadarEdgeData>>([]);

  const affected = useMemo(() => new Set(affectedServices.filter((service) => service !== rootCause)), [affectedServices, rootCause]);

  useEffect(() => {
    const rootNode = topologyNodes.find((node) => node.id === rootCause) ?? {
      id: rootCause,
      label: rootCause,
      type: "service" as const,
      status: "critical" as const,
    };
    const otherNodes = topologyNodes.filter((node) => node.id !== rootNode.id);
    const center = { x: 420, y: 215 };
    const radius = 205;
    const nextNodes: Node<RadarNodeData>[] = [
      { id: rootNode.id, type: "radar", position: center, data: { node: rootNode, status: "root", onSelect } },
      ...otherNodes.map((node, index) => {
        const angle = (Math.PI * 2 * index) / Math.max(otherNodes.length, 1) - Math.PI / 2;
        return {
          id: node.id,
          type: "radar",
          position: { x: center.x + Math.cos(angle) * radius, y: center.y + Math.sin(angle) * radius },
          data: { node, status: affected.has(node.id) ? ("affected" as const) : ("nominal" as const), onSelect },
        };
      }),
    ];
    const knownIds = new Set(nextNodes.map((node) => node.id));
    const nextEdges = topologyEdges
      .filter((edge) => knownIds.has(edge.source) && knownIds.has(edge.target))
      .map((edge) => {
        const isCascade = edge.source === rootNode.id || edge.target === rootNode.id || (affected.has(edge.source) && affected.has(edge.target));
        return {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          type: "cascade",
          data: { isCascade },
          markerEnd: { type: MarkerType.ArrowClosed, color: isCascade ? "#F59E0B" : "#475569", width: 14, height: 14 },
        };
      });
    setNodes(nextNodes);
    setEdges(nextEdges);
  }, [affected, onSelect, rootCause, setEdges, setNodes, topologyEdges, topologyNodes]);

  return (
    <div className="h-full w-full bg-[#0b1220]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
        minZoom={0.35}
        maxZoom={1.5}
        panOnDrag
        proOptions={{ hideAttribution: true }}
      >
        <AutoCenter rootId={rootCause} />
        <Background color="#1e293b" gap={22} size={1} />
        <Controls showInteractive={false} className="!border-[#334155] !bg-[#111827] [&>button]:!border-[#334155] [&>button]:!bg-[#111827] [&>button]:!fill-[#cbd5e1]" />
      </ReactFlow>
    </div>
  );
}

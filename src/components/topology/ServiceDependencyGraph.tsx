import { useMemo } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  getBezierPath,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import dagre from "dagre";
import { Database, Network, Radio, Server, Share2, Zap } from "lucide-react";
import { useTopology } from "../../hooks/useTopology";
import type { NodeType, TopologyNode } from "../../types/topology";
import "@xyflow/react/dist/style.css";

type DependencyStatus = "healthy" | "affected" | "root";

type DependencyNodeData = {
  node: TopologyNode;
  status: DependencyStatus;
};

type DependencyEdgeData = {
  kind: "traffic" | "failure";
  latency?: number;
};

const nodeIcons: Record<NodeType, typeof Server> = {
  gateway: Share2,
  service: Server,
  database: Database,
  cache: Zap,
  queue: Radio,
};

function DependencyNode({ data }: NodeProps<Node<DependencyNodeData>>) {
  const Icon = nodeIcons[data.node.type];
  const root = data.status === "root";
  const affected = data.status === "affected";
  const accent = root ? "#ef4444" : affected ? "#f59e0b" : "#388bfd";

  return (
    <div className="relative h-[80px] w-[174px]">
      <Handle id="traffic-in" type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-transparent" />
      <Handle id="failure-in" type="target" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-transparent" />
      {root && <span className="absolute -inset-1 rounded-full border border-[#ef4444]/50 animate-pulse" />}
      <div
        className="relative flex h-full items-center gap-2.5 rounded-full border bg-[#111827]/95 px-3.5 shadow-sm"
        style={{ borderColor: accent, boxShadow: root ? "0 0 20px rgba(239,68,68,0.35)" : affected ? "0 0 14px rgba(245,158,11,0.22)" : "none" }}
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full" style={{ color: accent, backgroundColor: `${accent}20` }}>
          <Icon className="h-4 w-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[11px] font-bold text-[#f8fafc]">{data.node.name}</span>
          <span className="mt-0.5 flex items-center gap-1.5 text-[8px] font-bold uppercase tracking-wide text-[#94a3b8]">
            <i className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: accent }} />
            {root ? "Root cause" : affected ? "Affected" : "Nominal"}
          </span>
          <span className="text-[#8b949e] font-mono text-[10px] bg-[#0d1117]/80 px-1.5 py-0.5 rounded border border-[#30363d] shadow-sm">
            {data.node.metrics?.latency_p95_ms?.toFixed(1)}ms
          </span>
        </span>
      </div>
      <Handle id="traffic-out" type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-transparent" />
      <Handle id="failure-out" type="source" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-transparent" />
    </div>
  );
}

function DependencyEdge(props: EdgeProps<Edge<DependencyEdgeData>>) {
  const [path, labelX, labelY] = getBezierPath({ ...props, curvature: 0.26 });
  const failure = props.data?.kind === "failure";
  const stroke = failure ? "#ef4444" : "#388bfd";

  return (
    <>
      <BaseEdge path={path} markerEnd={props.markerEnd} style={{ stroke, strokeWidth: failure ? 3 : 1.6, strokeDasharray: failure ? "8 5" : "5 8", opacity: failure ? 1 : 0.72 }} />
      <circle r={failure ? "3.8" : "2.5"} fill={stroke}>
        <animateMotion dur={failure ? "1s" : "2.6s"} repeatCount="indefinite" path={path} />
      </circle>
      {failure && (
        <EdgeLabelRenderer>
          <span
            className="nodrag nopan absolute rounded border border-[#ef4444]/50 bg-[#1f1015] px-1.5 py-0.5 font-mono text-[8px] font-bold uppercase tracking-wide text-[#fca5a5]"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, pointerEvents: "none" }}
          >
            failure
          </span>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { dependency: DependencyNode };
const edgeTypes = { dependency: DependencyEdge };

export function ServiceDependencyGraph({
  rootCause,
  affectedServices,
  failurePaths,
}: {
  rootCause?: string | null;
  affectedServices: string[];
  failurePaths: string[][];
}) {
  const { nodes: topologyNodes, edges: topologyEdges } = useTopology();
  const affected = useMemo(() => new Set([...affectedServices, ...failurePaths.flat()]), [affectedServices, failurePaths]);

  const { nodes, edges, degradedCount, failureCount } = useMemo(() => {
    const graph = new dagre.graphlib.Graph();
    graph.setDefaultEdgeLabel(() => ({}));
    graph.setGraph({ rankdir: "LR", ranksep: 126, nodesep: 68, marginx: 42, marginy: 42 });

    const statusFor = (id: string): DependencyStatus => {
      if (id === rootCause) return "root";
      return affected.has(id) ? "affected" : "healthy";
    };

    topologyNodes.forEach((node) => graph.setNode(node.id, { width: 174, height: 80 }));
    topologyEdges.forEach((edge) => graph.setEdge(edge.source, edge.target));
    dagre.layout(graph);

    const layoutNodes: Node<DependencyNodeData>[] = topologyNodes.map((node) => {
      const position = graph.node(node.id);
      return {
        id: node.id,
        type: "dependency",
        position: { x: position.x - 87, y: position.y - 40 },
        data: { node, status: statusFor(node.id) },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        selectable: false,
        draggable: false,
      };
    });

    const trafficEdges: Edge<DependencyEdgeData>[] = topologyEdges.map((edge) => {
      return {
        id: `traffic-${edge.id}`,
        source: edge.source,
        target: edge.target,
        type: "dependency",
        sourceHandle: "traffic-out",
        targetHandle: "traffic-in",
        data: { kind: "traffic", latency: edge.avg_latency_ms },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#388bfd", width: 13, height: 13 },
      };
    });

    const knownIds = new Set(topologyNodes.map((node) => node.id));
    const causalPairs = new Set<string>();
    for (const path of failurePaths) {
      for (let index = 0; index < path.length - 1; index += 1) {
        const source = path[index];
        const target = path[index + 1];
        if (knownIds.has(source) && knownIds.has(target) && source !== target) causalPairs.add(`${source}::${target}`);
      }
    }

    const layoutById = new Map(layoutNodes.map((node) => [node.id, node.position]));
    const failureEdges: Edge<DependencyEdgeData>[] = Array.from(causalPairs).map((pair) => {
      const [source, target] = pair.split("::");
      const sourcePosition = layoutById.get(source);
      const targetPosition = layoutById.get(target);
      const pointsRight = (sourcePosition?.x ?? 0) <= (targetPosition?.x ?? 0);
      return {
        id: `failure-${source}-${target}`,
        source,
        target,
        type: "dependency",
        sourceHandle: pointsRight ? "traffic-out" : "failure-out",
        targetHandle: pointsRight ? "traffic-in" : "failure-in",
        data: { kind: "failure" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#ef4444", width: 16, height: 16 },
        zIndex: 2,
      };
    });

    return { nodes: layoutNodes, edges: [...trafficEdges, ...failureEdges], degradedCount: layoutNodes.filter((node) => node.data.status !== "healthy").length, failureCount: failureEdges.length };
  }, [affected, failurePaths, rootCause, topologyEdges, topologyNodes]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#080e18]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.22, maxZoom: 1.15 }}
        minZoom={0.35}
        maxZoom={1.45}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#1e293b" gap={18} size={1} />
        <Panel position="top-left" className="!m-4 flex items-center gap-2 rounded-md border border-[#334155] bg-[#111827]/95 px-3 py-2 shadow-lg">
          <Network className="h-4 w-4 text-[#60a5fa]" />
          <span className="text-[12px] font-bold text-[#e6edf3]">Service Dependency Graph</span>
          <span className={`rounded-full border px-1.5 py-0.5 font-mono text-[9px] font-bold ${degradedCount > 0 ? "border-[#ef4444]/50 bg-[#ef4444]/15 text-[#fca5a5]" : "border-[#3fb950]/40 bg-[#3fb950]/10 text-[#3fb950]"}`}>
            {degradedCount > 0 ? `${degradedCount} AFFECTED` : "NOMINAL"}
          </span>
          {failureCount > 0 && <span className="hidden font-mono text-[9px] text-[#fca5a5] sm:inline">{failureCount} FAILURE LINKS</span>}
        </Panel>
        <Panel position="top-right" className="!m-4 hidden items-center gap-2 rounded-md border border-[#334155] bg-[#111827]/90 px-2.5 py-1.5 text-[9px] font-semibold text-[#94a3b8] sm:flex">
          <span className="text-[#64748b]">FLOW</span>
          <span className="font-mono text-[#cbd5e1]">INGRESS</span>
          <span className="text-[#475569]">&#8594;</span>
          <span className="font-mono text-[#cbd5e1]">SERVICES</span>
          <span className="text-[#475569]">&#8594;</span>
          <span className="font-mono text-[#cbd5e1]">DATA</span>
        </Panel>
        <Panel position="bottom-left" className="!m-4 flex gap-3 text-[9px] font-semibold text-[#94a3b8]">
          <span className="flex items-center gap-1"><i className="h-1.5 w-1.5 rounded-full bg-[#388bfd]" /> Dependency traffic</span>
          <span className="flex items-center gap-1"><i className="h-1.5 w-1.5 rounded-full bg-[#ef4444]" /> Failure propagation</span>
        </Panel>
        <Controls showInteractive={false} className="!border-[#334155] !bg-[#111827] [&>button]:!border-[#334155] [&>button]:!bg-[#111827] [&>button]:!fill-[#cbd5e1]" />
      </ReactFlow>
    </div>
  );
}

import { useEffect } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  Background,
  Controls,
  MarkerType,
  NodeProps
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useTelemetryStream } from '../../hooks/useTelemetryStream';
import { useTopologySync } from '../../hooks/useTopologySync';
import { useTopology } from '../../hooks/useTopology';
import { useThemeMode } from '../../hooks/useThemeMode';
import dagre from 'dagre';

const SEVERITY_COLORS: Record<string, string> = {
  critical: "#f85149", // red
  high: "#ffa657",     // orange
  medium: "#d29922",   // yellow
  low: "#7d8590",      // gray
  normal: "#3fb950"    // green
};

// Custom Node Component
function ServiceNode({ data }: NodeProps) {
  const isRoot = data.impactClassification === 'root';
  const color = SEVERITY_COLORS[data.severity as string] || SEVERITY_COLORS.normal;
  
  return (
    <div className={`px-4 py-2 rounded-lg border-2 bg-[#0d1117] text-[#e6edf3] flex flex-col items-center justify-center relative ${isRoot ? 'animate-pulse' : ''}`}
         style={{ borderColor: color, boxShadow: isRoot ? `0 0 15px ${color}` : 'none' }}>
      <Handle type="target" position={Position.Top} style={{ background: '#555' }} />
      <div className="text-sm font-bold">{data.serviceName as string}</div>
      <div className="text-xs text-[#7d8590] mt-1 uppercase tracking-widest">{data.severity as string}</div>
      {data.anomalyScore !== undefined && (
        <div className="text-[10px] text-[#7d8590] mt-1">Score: {Number(data.anomalyScore).toFixed(2)}</div>
      )}
      <Handle type="source" position={Position.Bottom} style={{ background: '#555' }} />
    </div>
  );
}

const nodeTypes = {
  serviceNode: ServiceNode,
};

function TopologyCanvasInner() {
  const { connectionStatus, activeTrackingLoops } = useTelemetryStream();
  const { selectedNodeId } = useTopologySync();
  const { setCenter } = useReactFlow();
  const { themeMode } = useThemeMode();
  const { topology } = useTopology(2000); // Fetch topology every 2s
  
  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);

  // Process tracking loops to build nodes and edges
  useEffect(() => {
    if (!topology || !topology.nodes) return;

    const newNodes: any[] = [];
    const newEdges: any[] = [];
    
    // Base topology mapping
    topology.nodes.forEach((node, index) => {
      newNodes.push({
        id: node.id,
        type: 'serviceNode',
        position: { x: 0, y: 0 },
        data: {
          serviceName: (node as any).service_name || (node as any).service || node.id,
          impactClassification: 'normal',
          severity: 'normal',
          anomalyScore: undefined,
        },
      });
    });

    topology.edges.forEach((edge) => {
      newEdges.push({
        id: `e-${edge.source}-${edge.target}`,
        source: edge.source,
        target: edge.target,
        animated: false,
        style: { stroke: '#888', strokeWidth: 1 },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: '#888',
        },
      });
    });

    // Merge active tracking loops
    if (activeTrackingLoops && activeTrackingLoops.length > 0) {
      activeTrackingLoops.forEach((loop: any) => {
        const blastNodes = Array.isArray(loop.blast_radius) 
          ? loop.blast_radius 
          : (loop.blast_radius?.blast_radius || []);
        
        const loopColor = SEVERITY_COLORS[loop.severity] || SEVERITY_COLORS.critical;

        blastNodes.forEach((bNode: any) => {
          // Find node and update
          const targetNode = newNodes.find(n => n.id === bNode.service_name);
          if (targetNode) {
            targetNode.data.impactClassification = bNode.impact_classification;
            targetNode.data.severity = loop.severity;
            targetNode.data.anomalyScore = bNode.impact_score;
          }

          // Find edges and update
          if (bNode.dependency_path && bNode.dependency_path.length > 0) {
            bNode.dependency_path.forEach((dep: string) => {
              const targetEdgeId = `e-${dep}-${bNode.service_name}`;
              let targetEdge = newEdges.find(e => e.id === targetEdgeId);
              
              if (!targetEdge) {
                targetEdge = {
                  id: targetEdgeId,
                  source: dep,
                  target: bNode.service_name,
                  animated: true,
                  style: { stroke: loopColor, strokeWidth: 2 },
                  markerEnd: { type: MarkerType.ArrowClosed, color: loopColor },
                };
                newEdges.push(targetEdge);
              } else {
                targetEdge.animated = true;
                targetEdge.style = { stroke: loopColor, strokeWidth: 2 };
                targetEdge.markerEnd.color = loopColor;
              }
            });
          }
        });
      });
    }

    if (newNodes.length > 0) {
      const g = new dagre.graphlib.Graph();
      g.setGraph({ rankdir: 'TB', nodesep: 80, ranksep: 100 });
      g.setDefaultEdgeLabel(() => ({}));

      newNodes.forEach((n) => {
        g.setNode(n.id, { width: 160, height: 80 });
      });
      newEdges.forEach((e) => {
        g.setEdge(e.source, e.target);
      });
      dagre.layout(g);

      newNodes.forEach((n) => {
        const nodeWithPosition = g.node(n.id);
        n.position = {
          x: nodeWithPosition.x - 80,
          y: nodeWithPosition.y - 40,
        };
      });

      setNodes(newNodes);
      setEdges(newEdges);
    }
  }, [topology, activeTrackingLoops, setNodes, setEdges]);

  // Handle cross-component sync panning
  useEffect(() => {
    if (selectedNodeId && nodes.length > 0) {
      const node = nodes.find(n => n.id === selectedNodeId);
      if (node) {
        setCenter(node.position.x, node.position.y, { zoom: 1.2, duration: 800 });
      }
    }
  }, [selectedNodeId, nodes, setCenter]);

  return (
    <div className="w-full h-full min-h-[400px] border border-[#21262d] rounded-xl bg-[#060c18] relative overflow-hidden">
      <div className="absolute top-4 left-4 z-10 flex items-center gap-2">
        <div className="text-[#e6edf3] font-semibold text-sm">Topology Canvas</div>
        <div className="flex items-center gap-1 text-xs">
          <span className={`w-2 h-2 rounded-full ${connectionStatus === 'connected' ? 'bg-green-500' : 'bg-red-500'}`}></span>
          <span className="text-gray-400">{connectionStatus}</span>
        </div>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        colorMode="dark"
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#21262d" gap={16} />
        <Controls />
      </ReactFlow>
    </div>
  );
}

export function TopologyCanvas() {
  return (
    <ReactFlowProvider>
      <TopologyCanvasInner />
    </ReactFlowProvider>
  );
}

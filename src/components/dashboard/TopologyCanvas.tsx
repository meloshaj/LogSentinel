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
    <div className={`px-4 py-2 rounded-lg border-2 bg-[#0d1117] text-white flex flex-col items-center justify-center relative ${isRoot ? 'animate-pulse' : ''}`}
         style={{ borderColor: color, boxShadow: isRoot ? `0 0 15px ${color}` : 'none' }}>
      <Handle type="target" position={Position.Top} style={{ background: '#555' }} />
      <div className="text-sm font-bold">{data.serviceName as string}</div>
      <div className="text-xs text-gray-400 mt-1 uppercase tracking-widest">{data.severity as string}</div>
      {data.anomalyScore !== undefined && (
        <div className="text-[10px] text-gray-500 mt-1">Score: {Number(data.anomalyScore).toFixed(2)}</div>
      )}
      <Handle type="source" position={Position.Bottom} style={{ background: '#555' }} />
    </div>
  );
}

const nodeTypes = {
  serviceNode: ServiceNode,
};

function TopologyCanvasInner() {
  const { activeTrackingLoops, connectionStatus } = useTelemetryStream();
  const { selectedNodeId } = useTopologySync();
  const { setCenter } = useReactFlow();
  
  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);

  // Process tracking loops to build nodes and edges
  useEffect(() => {
    if (!activeTrackingLoops || activeTrackingLoops.length === 0) {
      return;
    }

    const newNodes: any[] = [];
    const newEdges: any[] = [];
    const processedServices = new Set<string>();

    activeTrackingLoops.forEach((loop) => {
      if (!loop.blast_radius || !loop.blast_radius.blast_radius) return;

      const blastNodes = loop.blast_radius.blast_radius;
      
      blastNodes.forEach((node, idx) => {
        if (processedServices.has(node.service_name)) return;
        processedServices.add(node.service_name);
        
        // Approximate layout: root is at top, others spread below
        const isRoot = node.impact_classification === 'root';
        const yPos = isRoot ? 50 : 150 + (idx * 50);
        const xPos = 250 + (idx % 2 === 0 ? idx * 50 : -idx * 50);
        
        newNodes.push({
          id: node.service_name,
          type: 'serviceNode',
          position: { x: xPos, y: yPos },
          data: {
            serviceName: node.service_name,
            impactClassification: node.impact_classification,
            severity: loop.severity,
            anomalyScore: node.impact_score,
          },
        });

        // Edges from dependency_path or propagation_path
        if (node.dependency_path && node.dependency_path.length > 0) {
          node.dependency_path.forEach(dep => {
            newEdges.push({
              id: `e-${dep}-${node.service_name}`,
              source: dep,
              target: node.service_name,
              animated: true,
              style: { stroke: SEVERITY_COLORS[loop.severity] || '#888' },
              markerEnd: {
                type: MarkerType.ArrowClosed,
                color: SEVERITY_COLORS[loop.severity] || '#888',
              },
            });
          });
        }
      });
    });

    if (newNodes.length > 0) {
        setNodes(newNodes);
        setEdges(newEdges);
    }
  }, [activeTrackingLoops, setNodes, setEdges]);

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
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#21262d" gap={16} />
        <Controls style={{ fill: '#484f58' }} />
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

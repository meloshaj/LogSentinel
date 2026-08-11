import React from "react";
import { Handle, Position } from "@xyflow/react";
import type { TopologyNode as TNode, NodeStatus } from "../../types/topology";
import { Server, Database, Zap, Layers, Share2, Activity, Clock, AlertCircle } from "lucide-react";

const TYPE_ICONS: Record<string, React.ElementType> = {
  service: Server,
  database: Database,
  cache: Zap,
  queue: Layers,
  gateway: Share2,
};

const STATUS_CONFIG: Record<string, { border: string, bg: string, ring: string, text: string }> = {
  healthy: { border: "border-[#30363d]", bg: "bg-[#0d1117]", ring: "ring-0", text: "text-[#3fb950]" },
  degraded: { border: "border-[#d29922]", bg: "bg-[#d29922]/10", ring: "ring-1 ring-[#d29922]", text: "text-[#d29922]" },
  critical: { border: "border-[#f85149]", bg: "bg-[#f85149]/10", ring: "ring-2 ring-[#f85149] ring-offset-2 ring-offset-[#0d1117] animate-pulse", text: "text-[#f85149]" },
};

interface TopologyNodeProps {
  data: {
    node: TNode;
    status: string; // 'healthy' | 'degraded' | 'critical'
    isRoot: boolean;
    isPath?: boolean; // Highlight path for blast radius
    onNodeClick?: (id: string) => void;
  };
  selected?: boolean;
}

const TopologyNodeComponent: React.FC<TopologyNodeProps> = ({ data, selected }) => {
  const { node, status = "healthy", isRoot, isPath, onNodeClick } = data;
  
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.healthy;
  const Icon = TYPE_ICONS[node.type] || Server;
  
  const isSelected = selected;
  const isCritical = status === "critical";
  const isDegraded = status === "degraded";

  // Mock metrics if not present
  const metrics = {
    latency: Math.floor(Math.random() * 50) + 10,
    errorRate: isCritical ? (Math.random() * 5 + 5).toFixed(1) : (Math.random() * 0.5).toFixed(2),
    throughput: Math.floor(Math.random() * 2000) + 500,
  };

  return (
    <>
      <Handle type="target" position={Position.Top} className="w-2 h-2 !bg-[#484f58] border-none" />
      
      <div 
        className={`
          relative flex flex-col gap-2 px-3 py-3 min-w-[200px] rounded-xl cursor-pointer
          transition-all duration-300
          ${config.bg} border-2 ${isSelected ? "border-[#388bfd]" : config.border}
          ${isPath ? "ring-2 ring-[#f85149] ring-offset-2 ring-offset-[#0d1117] shadow-[0_0_15px_rgba(248,81,73,0.3)]" : config.ring} shadow-lg backdrop-blur-sm
          hover:brightness-110
        `}
        onClick={() => onNodeClick?.(node.id)}
      >
        {isRoot && (
          <div className="absolute -top-3 -right-3 w-6 h-6 rounded-full bg-[#f85149] flex items-center justify-center shadow-[0_0_12px_rgba(248,81,73,0.6)] z-10">
            <span className="text-white text-[10px] font-bold">R</span>
          </div>
        )}

        {/* Top Header Row */}
        <div className="flex items-start gap-3">
          <div className={`
            flex items-center justify-center w-10 h-10 rounded-lg shrink-0
            ${isCritical ? 'bg-[#f85149]/20 text-[#f85149]' : isDegraded ? 'bg-[#d29922]/20 text-[#d29922]' : 'bg-[#21262d] text-[#7d8590]'}
          `}>
            <Icon className="w-5 h-5" />
          </div>
          
          <div className="flex flex-col min-w-0 flex-1">
            <div className="flex items-center justify-between">
              <span className="text-[#e6edf3] font-semibold text-sm truncate">{node.label}</span>
              {status !== "healthy" && (
                <span className={`text-[10px] font-bold ${config.text} uppercase`}>
                  {status}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-[#7d8590] text-[10px] uppercase tracking-wider font-medium">{node.type}</span>
              <span className="px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wider font-bold bg-[#161b22] border border-[#30363d] text-[#c9d1d9]">
                PROD
              </span>
            </div>
          </div>
        </div>

        {/* Metrics Row */}
        <div className="grid grid-cols-3 gap-2 mt-2 pt-2 border-t border-[#30363d]/50">
          <div className="flex flex-col items-center">
            <div className="flex items-center gap-1 text-[#7d8590] mb-0.5">
              <Clock className="w-3 h-3" />
              <span className="text-[9px] uppercase font-semibold">Lat</span>
            </div>
            <span className={`text-[11px] font-mono ${isCritical ? 'text-[#f85149]' : 'text-[#c9d1d9]'}`}>{metrics.latency}ms</span>
          </div>
          <div className="flex flex-col items-center border-l border-r border-[#30363d]/50">
            <div className="flex items-center gap-1 text-[#7d8590] mb-0.5">
              <AlertCircle className="w-3 h-3" />
              <span className="text-[9px] uppercase font-semibold">Err</span>
            </div>
            <span className={`text-[11px] font-mono ${isCritical ? 'text-[#f85149]' : 'text-[#c9d1d9]'}`}>{metrics.errorRate}%</span>
          </div>
          <div className="flex flex-col items-center">
            <div className="flex items-center gap-1 text-[#7d8590] mb-0.5">
              <Activity className="w-3 h-3" />
              <span className="text-[9px] uppercase font-semibold">Ops</span>
            </div>
            <span className="text-[11px] font-mono text-[#c9d1d9]">{metrics.throughput}/s</span>
          </div>
        </div>
      </div>
      
      <Handle type="source" position={Position.Bottom} className="w-2 h-2 !bg-[#484f58] border-none" />
    </>
  );
};

TopologyNodeComponent.displayName = "TopologyNode";

export { TopologyNodeComponent as TopologyNode };

import React from "react";
import { Handle, Position } from "@xyflow/react";
import type { TopologyNode as TNode } from "../../types/topology";
import { Server, Database, Zap, Layers, Share2, Activity, Clock, AlertCircle, ShieldCheck, Target, Flame } from "lucide-react";

const TYPE_ICONS: Record<string, React.ElementType> = {
  service: Server,
  database: Database,
  cache: Zap,
  queue: Layers,
  gateway: Share2,
};

const STATUS_CONFIG: Record<string, { border: string, bg: string, ring: string, badgeBg: string, text: string, glow: string, label: string }> = {
  healthy: { 
    border: "border-[#30363d]", 
    bg: "bg-[#161b22]/95", 
    ring: "hover:ring-1 hover:ring-[#3fb950]/50", 
    badgeBg: "bg-[#3fb950]/15 border-[#3fb950]/30 text-[#3fb950]",
    text: "text-[#3fb950]", 
    glow: "shadow-sm",
    label: "NOMINAL" 
  },
  degraded: { 
    border: "border-[#f59e0b]/80", 
    bg: "bg-[#161b22]/95", 
    ring: "ring-1 ring-[#f59e0b]/70", 
    badgeBg: "bg-[#f59e0b]/20 border-[#f59e0b]/50 text-[#f59e0b]",
    text: "text-[#f59e0b]", 
    glow: "shadow-[0_0_18px_rgba(245,158,11,0.25)]",
    label: "BLAST CASCADE" 
  },
  critical: { 
    border: "border-[#ef4444]", 
    bg: "bg-[#161b22]/95", 
    ring: "ring-2 ring-[#ef4444] ring-offset-2 ring-offset-[#0d1117]", 
    badgeBg: "bg-[#ef4444]/25 border-[#ef4444]/60 text-[#ef4444]",
    text: "text-[#ef4444]", 
    glow: "shadow-[0_0_28px_rgba(239,68,68,0.45)] animate-pulse",
    label: "ROOT CAUSE" 
  },
};

interface TopologyNodeProps {
  data: {
    node: TNode;
    status: string; // 'healthy' | 'degraded' | 'critical'
    isRoot: boolean;
    isPath?: boolean;
    onNodeClick?: (id: string) => void;
  };
  selected?: boolean;
  targetPosition?: Position;
  sourcePosition?: Position;
}

const TopologyNodeComponent: React.FC<TopologyNodeProps> = ({ 
  data, 
  selected, 
  targetPosition = Position.Left, 
  sourcePosition = Position.Right 
}) => {
  const { node, status = "healthy", isRoot, isPath, onNodeClick } = data;
  
  const effectiveStatus = isRoot ? "critical" : status;
  const config = STATUS_CONFIG[effectiveStatus] || STATUS_CONFIG.healthy;
  const Icon = TYPE_ICONS[node.type] || Server;
  
  const isSelected = selected;
  const isCritical = effectiveStatus === "critical" || isRoot;
  const isDegraded = effectiveStatus === "degraded" && !isRoot;

  return (
    <>
      <Handle 
        type="target" 
        position={targetPosition} 
        className="w-3 h-3 !bg-[#388bfd] border-2 border-[#0d1117] transition-transform hover:scale-125" 
      />
      
      <div 
        className={`
          relative flex flex-col gap-2 px-3.5 py-3 min-w-[210px] rounded-xl cursor-pointer
          transition-all duration-200 backdrop-blur-md select-none
          ${config.bg} border ${isSelected ? "!border-[#388bfd] !ring-2 !ring-[#388bfd]/60" : config.border}
          ${isCritical ? "ring-2 ring-[#ef4444] shadow-[0_0_25px_rgba(239,68,68,0.4)]" : isDegraded ? "ring-1 ring-[#f59e0b]" : config.ring} ${config.glow}
          hover:border-[#8b949e] hover:shadow-lg
        `}
        onClick={() => onNodeClick?.(node.id)}
      >
        {isRoot && (
          <div className="absolute -top-3 -right-2 px-2 py-0.5 rounded-full bg-[#ef4444] text-white text-[9px] font-extrabold shadow-[0_0_14px_rgba(239,68,68,0.8)] z-10 flex items-center gap-1">
            <Target className="w-2.5 h-2.5 animate-spin" />
            ROOT CAUSE
          </div>
        )}

        {/* Top Header Row */}
        <div className="flex items-center gap-2.5">
          <div className={`
            flex items-center justify-center w-9 h-9 rounded-lg shrink-0 border
            ${isCritical 
              ? 'bg-[#ef4444]/25 border-[#ef4444]/50 text-[#ef4444]' 
              : isDegraded 
                ? 'bg-[#f59e0b]/20 border-[#f59e0b]/40 text-[#f59e0b]' 
                : 'bg-[#21262d] border-[#30363d] text-[#388bfd]'}
          `}>
            <Icon className="w-4 h-4" />
          </div>
          
          <div className="flex flex-col min-w-0 flex-1">
            <div className="flex items-center justify-between gap-1">
              <span className="text-[#e6edf3] font-bold text-xs truncate" title={node.name || node.id}>
                {node.name || node.id}
              </span>
              <span className={`px-1.5 py-0.5 rounded text-[8px] font-bold uppercase tracking-wider border shrink-0 ${config.badgeBg}`}>
                {isRoot ? "ROOT CAUSE" : config.label}
              </span>
            </div>
            
            <div className="flex items-center gap-1.5 mt-0.5 text-[10px]">
              <span className="text-[#8b949e] uppercase tracking-wider font-semibold">
                {node.type || "service"}
              </span>
              <span className="w-1 h-1 rounded-full bg-[#30363d]" />
              <span className="text-[#7d8590] font-mono">
                {isRoot ? "Primary Initiator" : isDegraded ? "Downstream Cascade" : "Nominal State"}
              </span>
            </div>
          </div>
        </div>

        {/* Metrics Pill Grid */}
        <div className="grid grid-cols-3 gap-1 pt-1.5 border-t border-[#21262d]">
          <div className="flex flex-col items-center bg-[#0d1117] py-0.5 px-1 rounded border border-[#21262d]">
            <div className="flex items-center gap-0.5 text-[#7d8590]">
              <Clock className="w-2 h-2" />
              <span className="text-[7.5px] uppercase font-bold">Latency</span>
            </div>
            <span className={`text-[9.5px] font-mono font-bold ${isCritical ? 'text-[#ef4444]' : 'text-[#c9d1d9]'}`}>
              {node.metrics?.latency_p95_ms ?? (isCritical ? 5120 : 12)}ms
            </span>
          </div>

          <div className="flex flex-col items-center bg-[#0d1117] py-0.5 px-1 rounded border border-[#21262d]">
            <div className="flex items-center gap-0.5 text-[#7d8590]">
              <AlertCircle className="w-2 h-2" />
              <span className="text-[7.5px] uppercase font-bold">Errors</span>
            </div>
            <span className={`text-[9.5px] font-mono font-bold ${isCritical ? 'text-[#ef4444]' : isDegraded ? 'text-[#f59e0b]' : 'text-[#3fb950]'}`}>
              {node.metrics?.error_rate_pct ?? (isCritical ? 8.4 : isDegraded ? 2.1 : 0.0)}%
            </span>
          </div>

          <div className="flex flex-col items-center bg-[#0d1117] py-0.5 px-1 rounded border border-[#21262d]">
            <div className="flex items-center gap-0.5 text-[#7d8590]">
              <Activity className="w-2 h-2" />
              <span className="text-[7.5px] uppercase font-bold">Flow</span>
            </div>
            <span className="text-[9.5px] font-mono font-bold text-[#388bfd]">
              {node.metrics?.throughput_rps ?? "1.2k"}/s
            </span>
          </div>
        </div>
      </div>
      
      <Handle 
        type="source" 
        position={sourcePosition} 
        className="w-3 h-3 !bg-[#388bfd] border-2 border-[#0d1117] transition-transform hover:scale-125" 
      />
    </>
  );
};

TopologyNodeComponent.displayName = "TopologyNode";

export { TopologyNodeComponent as TopologyNode };

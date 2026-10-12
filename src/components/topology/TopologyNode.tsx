import React from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeStatus, TopologyNode as TNode } from "../../types/topology";
import { Activity, AlertCircle, Clock, Database, Layers, Server, Share2, Target, Zap } from "lucide-react";

const TYPE_ICONS: Record<string, React.ElementType> = {
  service: Server,
  database: Database,
  cache: Zap,
  queue: Layers,
  gateway: Share2,
};

type DisplayStatus = NodeStatus | "unknown";

const STATUS_CONFIG: Record<DisplayStatus, { border: string; bg: string; ring: string; badgeBg: string; text: string; glow: string; label: string }> = {
  healthy: {
    border: "border-[#30363d]", bg: "bg-[#161b22]/95", ring: "hover:ring-1 hover:ring-[#3fb950]/50",
    badgeBg: "bg-[#3fb950]/15 border-[#3fb950]/30 text-[#3fb950]", text: "text-[#3fb950]", glow: "shadow-sm", label: "HEALTHY",
  },
  degraded: {
    border: "border-[#f59e0b]/80", bg: "bg-[#161b22]/95", ring: "ring-1 ring-[#f59e0b]/70",
    badgeBg: "bg-[#f59e0b]/20 border-[#f59e0b]/50 text-[#f59e0b]", text: "text-[#f59e0b]", glow: "shadow-[0_0_18px_rgba(245,158,11,0.25)]", label: "DEGRADED",
  },
  critical: {
    border: "border-[#ef4444]", bg: "bg-[#161b22]/95", ring: "ring-2 ring-[#ef4444] ring-offset-2 ring-offset-[#0d1117]",
    badgeBg: "bg-[#ef4444]/25 border-[#ef4444]/60 text-[#ef4444]", text: "text-[#ef4444]", glow: "shadow-[0_0_28px_rgba(239,68,68,0.45)]", label: "CRITICAL",
  },
  unknown: {
    border: "border-[#64748b]", bg: "bg-[#161b22]/95", ring: "ring-1 ring-[#64748b]/50",
    badgeBg: "bg-[#64748b]/20 border-[#64748b]/50 text-[#cbd5e1]", text: "text-[#cbd5e1]", glow: "shadow-sm", label: "UNKNOWN",
  },
};

interface TopologyNodeProps {
  data: {
    node: TNode;
    status: DisplayStatus;
    isRoot: boolean;
    isPath?: boolean;
    onNodeClick?: (id: string) => void;
  };
  selected?: boolean;
  targetPosition?: Position;
  sourcePosition?: Position;
}

function displayMetric(value: unknown, suffix: string) {
  return typeof value === "number" && Number.isFinite(value) ? `${value}${suffix}` : "Unavailable";
}

const TopologyNodeComponent: React.FC<TopologyNodeProps> = ({
  data,
  selected,
  targetPosition = Position.Left,
  sourcePosition = Position.Right,
}) => {
  const { node, status, isRoot, onNodeClick } = data;
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown;
  const Icon = TYPE_ICONS[node.type] ?? Server;
  const isCritical = status === "critical";
  const isDegraded = status === "degraded";
  const statusDescription = status === "healthy" ? "Authoritative healthy state" : status === "unknown" ? "Authoritative state unavailable" : status;

  return (
    <>
      <Handle type="target" position={targetPosition} className="h-3 w-3 !border-2 !border-[#0d1117] !bg-[#388bfd] transition-transform hover:scale-125" />
      <button
        type="button"
        aria-label={`Inspect ${node.name}; status ${config.label.toLowerCase()}`}
        className={`relative flex min-w-[210px] cursor-pointer select-none flex-col gap-2 rounded-xl px-3.5 py-3 text-left backdrop-blur-md transition-all duration-200 ${config.bg} border ${selected ? "!border-[#388bfd] !ring-2 !ring-[#388bfd]/60" : config.border} ${isCritical ? "ring-2 ring-[#ef4444] shadow-[0_0_25px_rgba(239,68,68,0.4)]" : isDegraded ? "ring-1 ring-[#f59e0b]" : config.ring} ${config.glow} hover:border-[#8b949e] hover:shadow-lg`}
        onClick={() => onNodeClick?.(node.id)}
      >
        {isRoot ? (
          <div className="absolute -right-2 -top-3 z-10 flex items-center gap-1 rounded-full bg-[#ef4444] px-2 py-0.5 text-[9px] font-extrabold text-white shadow-[0_0_14px_rgba(239,68,68,0.8)]">
            <Target className="h-2.5 w-2.5" /> ROOT CAUSE
          </div>
        ) : null}

        <div className="flex items-center gap-2.5">
          <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${isCritical ? "border-[#ef4444]/50 bg-[#ef4444]/25 text-[#ef4444]" : isDegraded ? "border-[#f59e0b]/40 bg-[#f59e0b]/20 text-[#f59e0b]" : "border-[#30363d] bg-[#21262d] text-[#388bfd]"}`}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex items-center justify-between gap-1">
              <span className="truncate text-xs font-bold text-[#e6edf3]" title={node.name}>{node.name}</span>
              <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wider ${config.badgeBg}`}>
                {isRoot ? "ROOT CAUSE" : config.label}
              </span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[10px]">
              <span className="font-semibold uppercase tracking-wider text-[#8b949e]">{node.type}</span>
              <span className="h-1 w-1 rounded-full bg-[#30363d]" />
              <span className="font-mono text-[#7d8590]">{statusDescription}</span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-1 border-t border-[#21262d] pt-1.5">
          <div className="flex flex-col items-center rounded border border-[#21262d] bg-[#0d1117] px-1 py-0.5">
            <div className="flex items-center gap-0.5 text-[#7d8590]"><Clock className="h-2 w-2" /><span className="text-[7.5px] font-bold uppercase">Latency</span></div>
            <span className={`font-mono text-[9.5px] font-bold ${isCritical ? "text-[#ef4444]" : "text-[#c9d1d9]"}`}>{displayMetric(node.metrics?.latency_p95_ms, "ms")}</span>
          </div>
          <div className="flex flex-col items-center rounded border border-[#21262d] bg-[#0d1117] px-1 py-0.5">
            <div className="flex items-center gap-0.5 text-[#7d8590]"><AlertCircle className="h-2 w-2" /><span className="text-[7.5px] font-bold uppercase">Errors</span></div>
            <span className={`font-mono text-[9.5px] font-bold ${isCritical ? "text-[#ef4444]" : isDegraded ? "text-[#f59e0b]" : "text-[#c9d1d9]"}`}>{displayMetric(node.metrics?.error_rate_pct, "%")}</span>
          </div>
          <div className="flex flex-col items-center rounded border border-[#21262d] bg-[#0d1117] px-1 py-0.5">
            <div className="flex items-center gap-0.5 text-[#7d8590]"><Activity className="h-2 w-2" /><span className="text-[7.5px] font-bold uppercase">Flow</span></div>
            <span className="font-mono text-[9.5px] font-bold text-[#388bfd]">{displayMetric(node.metrics?.throughput_rps, "/s")}</span>
          </div>
        </div>
      </button>
      <Handle type="source" position={sourcePosition} className="h-3 w-3 !border-2 !border-[#0d1117] !bg-[#388bfd] transition-transform hover:scale-125" />
    </>
  );
};

TopologyNodeComponent.displayName = "TopologyNode";

export { TopologyNodeComponent as TopologyNode };

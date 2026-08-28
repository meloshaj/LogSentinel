import React from "react";
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps, type Edge } from "@xyflow/react";

const STATUS_CONFIG: Record<string, { stroke: string, class: string, speed: string, dotFill: string }> = {
  healthy: { stroke: "#388bfd", class: "opacity-75", speed: "3s", dotFill: "#388bfd" },
  degraded: { stroke: "#f59e0b", class: "animate-pulse", speed: "1.6s", dotFill: "#f59e0b" },
  critical: { stroke: "#ef4444", class: "animate-pulse", speed: "0.9s", dotFill: "#ef4444" },
};

type CustomEdge = Edge<{ status?: string; latency_ms?: number }, 'custom'>;

export function TopologyEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  markerEnd,
  data,
}: EdgeProps<CustomEdge>) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.25,
  });

  const status = data?.status || "healthy";
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.healthy;
  const isCritical = status === "critical";
  const isDegraded = status === "degraded";

  return (
    <>
      <BaseEdge 
        path={edgePath} 
        markerEnd={markerEnd} 
        style={{
          ...style,
          strokeWidth: isCritical ? 2.5 : isDegraded ? 2 : 1.8,
          stroke: config.stroke,
          strokeDasharray: isCritical ? "5 3" : isDegraded ? "4 2" : undefined,
        }} 
        className={config.class}
      />
      
      {/* Animated particle flowing along dependency pathway */}
      <circle 
        r={isCritical ? "4.0" : isDegraded ? "3.5" : "2.8"} 
        fill={config.dotFill}
        filter={isCritical || isDegraded ? "drop-shadow(0 0 6px currentColor)" : undefined}
      >
        <animateMotion dur={config.speed} repeatCount="indefinite" path={edgePath} />
      </circle>
      
      {data?.latency_ms != null && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: 'all',
            }}
            className={`px-1.5 py-0.5 rounded text-[9px] font-mono font-bold shadow-md border ${
              isCritical 
                ? "bg-[#ef4444]/20 border-[#ef4444]/50 text-[#ef4444]" 
                : isDegraded 
                  ? "bg-[#f59e0b]/20 border-[#f59e0b]/50 text-[#f59e0b]" 
                  : "bg-[#0d1117] border-[#21262d] text-[#8b949e]"
            }`}
          >
            {data.latency_ms}ms
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

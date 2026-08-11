import React from "react";
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps, type Edge } from "@xyflow/react";

const STATUS_CONFIG: Record<string, { stroke: string, class: string }> = {
  healthy: { stroke: "#21262d", class: "" },
  degraded: { stroke: "#d29922", class: "animate-pulse" },
  critical: { stroke: "#f85149", class: "animate-pulse" },
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
  });

  const status = data?.status || "healthy";
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.healthy;
  const isHealthy = status === "healthy";

  return (
    <>
      <BaseEdge 
        path={edgePath} 
        markerEnd={markerEnd} 
        style={{
          ...style,
          strokeWidth: isHealthy ? 1.5 : 2,
          stroke: config.stroke,
        }} 
        className={config.class}
      />
      
      {/* Animated dots for traffic */}
      <circle r="3" fill={isHealthy ? "#388bfd" : config.stroke}>
        <animateMotion dur="2s" repeatCount="indefinite" path={edgePath} />
      </circle>
      
      {data?.latency_ms != null && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              fontSize: 10,
              pointerEvents: 'all',
            }}
            className="px-1.5 py-0.5 rounded bg-[#0d1117] border border-[#21262d] text-[#7d8590] font-mono shadow-sm"
          >
            {data.latency_ms}ms
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

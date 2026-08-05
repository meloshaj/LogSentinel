import React from "react";
import type { TopologyEdge as TEdge, EdgeStatus } from "../../types/topology";

// ---------------------------------------------------------------------------
// Edge stroke styling
// ---------------------------------------------------------------------------

const STATUS_STROKE: Record<EdgeStatus, string> = {
  normal: "#21262d",
  stressed: "#d29922",
  failing: "#f85149",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface Props {
  edge: TEdge;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

const TopologyEdgeComponent: React.FC<Props> = React.memo(
  ({ edge, x1, y1, x2, y2 }) => {
    const stroke = edge.status
      ? STATUS_STROKE[edge.status] ?? "#21262d"
      : "#21262d";

    const statusClass = edge.status ? `topo-edge--${edge.status}` : "";

    // Compute a slight curve via a control point offset
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const dx = x2 - x1;
    const dy = y2 - y1;
    const perpX = -dy * 0.12;
    const perpY = dx * 0.12;
    const cx = mx + perpX;
    const cy = my + perpY;

    const pathD = `M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`;

    // Arrowhead at target
    const angle = Math.atan2(y2 - cy, x2 - cx);
    const arrowLen = 8;
    const a1x = x2 - arrowLen * Math.cos(angle - Math.PI / 7);
    const a1y = y2 - arrowLen * Math.sin(angle - Math.PI / 7);
    const a2x = x2 - arrowLen * Math.cos(angle + Math.PI / 7);
    const a2y = y2 - arrowLen * Math.sin(angle + Math.PI / 7);

    return (
      <g
        id={`topo-edge-${edge.id}`}
        data-edge-id={edge.id}
        data-testid={`topo-edge-${edge.id}`}
      >
        <path
          d={pathD}
          fill="none"
          stroke={stroke}
          strokeWidth={1.5}
          className={statusClass}
        />
        <polygon
          points={`${x2},${y2} ${a1x},${a1y} ${a2x},${a2y}`}
          fill={stroke}
        />
        {/* Latency label (if available) */}
        {edge.latency_ms != null && (
          <text
            x={cx}
            y={cy - 6}
            textAnchor="middle"
            style={{
              fontSize: "8px",
              fill: "#484f58",
              fontFamily: "monospace",
            }}
          >
            {edge.latency_ms}ms
          </text>
        )}
      </g>
    );
  },
);

TopologyEdgeComponent.displayName = "TopologyEdge";

export { TopologyEdgeComponent as TopologyEdge };

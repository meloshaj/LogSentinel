import React from "react";
import type { TopologyNode as TNode, NodeStatus } from "../../types/topology";

// ---------------------------------------------------------------------------
// Node type → SVG icon path
// ---------------------------------------------------------------------------

const TYPE_ICONS: Record<string, string> = {
  service:
    "M4 4h16v12H4zM8 16v4M16 16v4M1 20h22", // server box
  database:
    "M12 2C6.48 2 2 3.79 2 6v12c0 2.21 4.48 4 10 4s10-1.79 10-4V6c0-2.21-4.48-4-10-4z", // cylinder
  cache:
    "M13 2L3 14h9l-1 8 10-12h-9l1-8z", // lightning
  queue:
    "M3 6h18v4H3zM3 14h18v4H3z", // stacked bars
  gateway:
    "M12 2l10 10-10 10L2 12z", // diamond
};

const STATUS_BORDER: Record<NodeStatus, string> = {
  healthy: "#30363d",
  degraded: "#d29922",
  critical: "#f85149",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface Props {
  node: TNode;
  x: number;
  y: number;
  selected?: boolean;
  onClick?: (nodeId: string) => void;
}

const TopologyNodeComponent: React.FC<Props> = React.memo(
  ({ node, x, y, selected, onClick }) => {
    const border = STATUS_BORDER[node.status] ?? "#30363d";
    const statusClass = `topo-node--${node.status}`;

    return (
      <g
        id={`topo-node-${node.id}`}
        data-node-id={node.id}
        data-testid={`topo-node-${node.id}`}
        className={`topo-node-group ${statusClass}`}
        transform={`translate(${x}, ${y})`}
        onClick={() => onClick?.(node.id)}
      >
        {/* Blast-radius ring (hidden by default, shown imperatively) */}
        <circle
          className="topo-blast-ring"
          cx={0}
          cy={0}
          r={22}
          fill="none"
          stroke="#f85149"
          strokeWidth={1.5}
          opacity={0}
          data-blast-ring={node.id}
        />

        {/* Main node body */}
        <rect
          x={-44}
          y={-24}
          width={88}
          height={48}
          rx={8}
          fill="#0d1117"
          stroke={selected ? "#388bfd" : border}
          strokeWidth={selected ? 2 : 1.5}
        />

        {/* Type icon */}
        <svg
          x={-38}
          y={-16}
          width={14}
          height={14}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="topo-node-icon"
        >
          <path d={TYPE_ICONS[node.type] ?? TYPE_ICONS.service} />
        </svg>

        {/* Label */}
        <text
          x={0}
          y={-4}
          textAnchor="middle"
          className="topo-node-label"
          style={{
            fontSize: "11px",
            fontWeight: 600,
            fill: "#e6edf3",
            fontFamily:
              "Inter, -apple-system, BlinkMacSystemFont, sans-serif",
          }}
        >
          {node.label.length > 14
            ? `${node.label.slice(0, 12)}…`
            : node.label}
        </text>

        {/* Sublabel: type + status */}
        <text
          x={0}
          y={12}
          textAnchor="middle"
          className="topo-node-sublabel"
          style={{
            fontSize: "8px",
            fontWeight: 500,
            fill: "#484f58",
            textTransform: "uppercase" as const,
            letterSpacing: "0.06em",
            fontFamily:
              "Inter, -apple-system, BlinkMacSystemFont, sans-serif",
          }}
        >
          {node.type}
        </text>
      </g>
    );
  },
);

TopologyNodeComponent.displayName = "TopologyNode";

export { TopologyNodeComponent as TopologyNode };

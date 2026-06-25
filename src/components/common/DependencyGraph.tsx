import { useEffect, useRef } from "react";
import { DEPENDENCY_NODE_STATUS } from "../../constants/statusConfig";
import type { ServiceGraph } from "../../types/monitoring";

interface DependencyGraphProps {
  graph: ServiceGraph;
  glowRadius?: number;
  nodeRadius?: number;
  strokeWidth?: number;
  labelOffset?: number;
}

export function DependencyGraph({
  graph,
  glowRadius = 18,
  nodeRadius = 13,
  strokeWidth = 1.5,
  labelOffset = 26,
}: DependencyGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const width = canvas.offsetWidth;
    const height = canvas.offsetHeight;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    const nodeMap: Record<string, { x: number; y: number }> = {};
    graph.nodes.forEach((node) => {
      nodeMap[node.id] = { x: node.x * (width / 400), y: node.y * (height / 400) };
    });

    ctx.clearRect(0, 0, width, height);

    graph.edges.forEach((edge) => {
      const from = nodeMap[edge.from];
      const to = nodeMap[edge.to];
      if (!from || !to) return;

      const isCritical = edge.to === "database-service" || edge.from === "database-service";
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x, to.y);
      ctx.strokeStyle = isCritical ? "rgba(248,81,73,0.5)" : "rgba(33,38,45,1)";
      ctx.lineWidth = isCritical ? 1.5 : 1;
      ctx.setLineDash(isCritical ? [4, 3] : []);
      ctx.stroke();
      ctx.setLineDash([]);

      const angle = Math.atan2(to.y - from.y, to.x - from.x);
      const midX = (from.x + to.x) / 2;
      const midY = (from.y + to.y) / 2;
      ctx.beginPath();
      ctx.moveTo(midX, midY);
      ctx.lineTo(midX - 6 * Math.cos(angle - 0.4), midY - 6 * Math.sin(angle - 0.4));
      ctx.lineTo(midX - 6 * Math.cos(angle + 0.4), midY - 6 * Math.sin(angle + 0.4));
      ctx.closePath();
      ctx.fillStyle = isCritical ? "rgba(248,81,73,0.5)" : "rgba(72,79,88,0.7)";
      ctx.fill();
    });

    graph.nodes.forEach((node) => {
      const pos = nodeMap[node.id];
      const config = DEPENDENCY_NODE_STATUS[node.id] ?? { fill: "#1c2128", stroke: "#484f58" };
      const isCritical = node.id === "database-service" || node.id === "payment-service";

      if (isCritical) {
        ctx.beginPath();
        ctx.arc(pos.x, pos.y, glowRadius, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(218,54,51,0.12)";
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(pos.x, pos.y, nodeRadius, 0, Math.PI * 2);
      ctx.fillStyle = config.fill;
      ctx.fill();
      ctx.strokeStyle = config.stroke;
      ctx.lineWidth = strokeWidth;
      ctx.stroke();

      ctx.fillStyle = "#c9d1d9";
      ctx.font = "500 9px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(node.id.split("-")[0], pos.x, pos.y + labelOffset);
    });
  }, [glowRadius, graph, labelOffset, nodeRadius, strokeWidth]);

  return <canvas ref={canvasRef} className="w-full h-full" style={{ display: "block" }} aria-label="Service dependency graph" />;
}

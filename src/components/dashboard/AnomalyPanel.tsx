import { mockAnomalies } from "../../services/mockMonitoringData";
import type { ServiceAnomaly } from "../../types/monitoring";
import { AlertTriangle, CheckCircle, TrendingUp, Zap } from "lucide-react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip } from "recharts";

const STATUS_CONFIG = {
  Critical: { color: "#f85149", bg: "bg-[#da3633]/15", border: "border-[#da3633]/40", dot: "bg-[#f85149]", label: "text-[#f85149]" },
  Warning:  { color: "#d29922", bg: "bg-[#d29922]/15", border: "border-[#d29922]/30", dot: "bg-[#d29922]", label: "text-[#e3b341]" },
  Normal:   { color: "#3fb950", bg: "bg-[#3fb950]/10", border: "border-[#3fb950]/20", dot: "bg-[#3fb950]", label: "text-[#3fb950]" },
};

function ScoreBar({ score, status }: { score: number; status: ServiceAnomaly["status"] }) {
  const cfg = STATUS_CONFIG[status];
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-[#21262d] overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${score * 100}%`, background: cfg.color }}
        />
      </div>
      <span style={{ fontSize: "11px", fontWeight: 600, color: cfg.color, minWidth: 32, textAlign: "right" }}>
        {(score * 100).toFixed(0)}%
      </span>
    </div>
  );
}

function ServiceCard({ anomaly }: { anomaly: ServiceAnomaly }) {
  const cfg = STATUS_CONFIG[anomaly.status];
  const isCritical = anomaly.status === "Critical";

  return (
    <div className={`p-3 rounded-lg border ${cfg.bg} ${cfg.border} ${isCritical ? "ring-1 ring-[#da3633]/30" : ""}`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-2 h-2 rounded-full shrink-0 ${cfg.dot} ${isCritical ? "animate-pulse" : ""}`} />
          <span className="text-[#e6edf3] truncate" style={{ fontSize: "12px", fontWeight: 600 }}>{anomaly.name}</span>
        </div>
        <span className={`shrink-0 px-1.5 py-0.5 rounded-md ${cfg.bg} ${cfg.label} border ${cfg.border}`} style={{ fontSize: "10px", fontWeight: 700 }}>
          {anomaly.status.toUpperCase()}
        </span>
      </div>

      <ScoreBar score={anomaly.score} status={anomaly.status} />

      <p className="mt-1.5 text-[#7d8590]" style={{ fontSize: "10px", lineHeight: 1.4 }}>
        {anomaly.explanation}
      </p>

      <div className="flex gap-3 mt-2">
        <span className="text-[#484f58]" style={{ fontSize: "10px" }}>
          Error: <span style={{ color: anomaly.errorRate > 10 ? "#f85149" : "#7d8590" }}>{anomaly.errorRate}%</span>
        </span>
        <span className="text-[#484f58]" style={{ fontSize: "10px" }}>
          P95: <span style={{ color: anomaly.latency > 500 ? "#d29922" : "#7d8590" }}>{anomaly.latency}ms</span>
        </span>
      </div>
    </div>
  );
}

const chartData = mockAnomalies.map((a) => ({ name: a.name.split("-")[0], score: Math.round(a.score * 100) }));

export function AnomalyPanel() {
  const criticalCount = mockAnomalies.filter((a) => a.status === "Critical").length;

  return (
    <div className="flex flex-col rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] shrink-0">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-[#d29922]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Anomaly Detection</span>
          {criticalCount > 0 && (
            <span className="px-1.5 py-0.5 rounded-full bg-[#da3633] text-white" style={{ fontSize: "10px", fontWeight: 700 }}>
              {criticalCount} critical
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 text-[#3fb950]">
          <TrendingUp className="w-3 h-3" />
          <span style={{ fontSize: "10px", fontWeight: 500 }}>ML model active</span>
        </div>
      </div>

      {/* Bar chart overview */}
      <div className="px-4 pt-3 pb-1">
        <div className="text-[#484f58] mb-1.5" style={{ fontSize: "10px" }}>Anomaly Score by Service</div>
        <div style={{ height: 80 }}>
          <ResponsiveContainer width="100%" height={80}>
            <BarChart data={chartData} barSize={14} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
              <Tooltip
                key="tooltip"
                contentStyle={{ background: "#0d1117", border: "1px solid #21262d", borderRadius: 6, fontSize: 10 }}
                labelStyle={{ color: "#7d8590" }}
                itemStyle={{ color: "#e6edf3" }}
              />
              <Bar key="score" dataKey="score" radius={[3, 3, 0, 0]}>
                {chartData.map((entry, idx) => {
                  const a = mockAnomalies[idx];
                  const color = a.status === "Critical" ? "#da3633" : a.status === "Warning" ? "#d29922" : "#3fb950";
                  return <Cell key={idx} fill={color} fillOpacity={0.85} />;
                })}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Service cards */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-2">
        {mockAnomalies.map((a) => (
          <ServiceCard key={a.id} anomaly={a} />
        ))}
      </div>
    </div>
  );
}

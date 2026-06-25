import { mockAnomalies, mockTimeSeriesData } from "../services/mockMonitoringData";
import type { ServiceAnomaly } from "../types/monitoring";
import { AlertTriangle, ArrowRight, TrendingUp, Zap } from "lucide-react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const STATUS_CONFIG = {
  Critical: { color: "#f85149", bg: "bg-[#da3633]/10", border: "border-[#da3633]/30", dot: "bg-[#f85149] animate-pulse" },
  Warning:  { color: "#d29922", bg: "bg-[#d29922]/10", border: "border-[#d29922]/20", dot: "bg-[#d29922]" },
  Normal:   { color: "#3fb950", bg: "bg-[#3fb950]/10", border: "border-[#3fb950]/20", dot: "bg-[#3fb950]" },
};

function AnomalyCard({ anomaly }: { anomaly: ServiceAnomaly }) {
  const cfg = STATUS_CONFIG[anomaly.status];
  const pct = Math.round(anomaly.score * 100);

  return (
    <div className={`p-4 rounded-xl border ${cfg.bg} ${cfg.border}`}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2.5">
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${cfg.dot}`} />
          <span className="text-[#e6edf3]" style={{ fontSize: "14px", fontWeight: 700 }}>{anomaly.name}</span>
        </div>
        <span
          className="shrink-0 px-2 py-1 rounded-md"
          style={{ fontSize: "10px", fontWeight: 700, color: cfg.color, background: `${cfg.color}20`, border: `1px solid ${cfg.color}40` }}
        >
          {anomaly.status.toUpperCase()}
        </span>
      </div>

      {/* Score bar */}
      <div className="mb-3">
        <div className="flex justify-between mb-1">
          <span className="text-[#484f58]" style={{ fontSize: "10px" }}>Anomaly Score</span>
          <span style={{ fontSize: "12px", fontWeight: 700, color: cfg.color }}>{pct}%</span>
        </div>
        <div className="h-2 rounded-full bg-[#21262d] overflow-hidden">
          <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: cfg.color }} />
        </div>
      </div>

      {/* Metrics row */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        {[
          { label: "Error Rate", value: `${anomaly.errorRate}%`, alert: anomaly.errorRate > 10 },
          { label: "P95 Latency", value: `${anomaly.latency}ms`, alert: anomaly.latency > 500 },
          { label: "Score", value: anomaly.score.toFixed(2), alert: anomaly.score > 0.6 },
        ].map((m) => (
          <div key={m.label} className="p-2 rounded-lg bg-[#0d1117] text-center">
            <div style={{ fontSize: "13px", fontWeight: 700, color: m.alert ? cfg.color : "#e6edf3" }}>{m.value}</div>
            <div className="text-[#484f58]" style={{ fontSize: "9px" }}>{m.label}</div>
          </div>
        ))}
      </div>

      <p className="text-[#7d8590]" style={{ fontSize: "11px", lineHeight: 1.5 }}>{anomaly.explanation}</p>
    </div>
  );
}

export function AnomaliesPage() {
  const critical = mockAnomalies.filter((a) => a.status === "Critical");
  const warning = mockAnomalies.filter((a) => a.status === "Warning");
  const normal = mockAnomalies.filter((a) => a.status === "Normal");

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Anomaly Detection</h1>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>ML-powered anomaly scoring across all services - updated in real-time</p>
      </div>

      {/* Summary strip */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: "Critical Services", count: critical.length, color: "#f85149", bg: "bg-[#da3633]/10 border-[#da3633]/30" },
          { label: "Warning Services",  count: warning.length,  color: "#d29922", bg: "bg-[#d29922]/10 border-[#d29922]/20" },
          { label: "Normal Services",   count: normal.length,   color: "#3fb950", bg: "bg-[#3fb950]/10 border-[#3fb950]/20" },
        ].map((s) => (
          <div key={s.label} className={`flex items-center justify-between p-4 rounded-xl border ${s.bg}`}>
            <span className="text-[#7d8590]" style={{ fontSize: "12px" }}>{s.label}</span>
            <span style={{ fontSize: "26px", fontWeight: 800, color: s.color }}>{s.count}</span>
          </div>
        ))}
      </div>

      {/* Anomaly trend chart */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <TrendingUp className="w-4 h-4 text-[#d29922]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Anomaly Score Trend</span>
        </div>
        <div className="px-4 pb-4 pt-2">
          <ResponsiveContainer width="100%" height={140}>
            <AreaChart data={mockTimeSeriesData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="anom-grad-errors" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#da3633" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#da3633" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="anom-grad-anomalies" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#d29922" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#d29922" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid key="grid" strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis key="x" dataKey="time" tick={{ fill: "#484f58", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis key="y" tick={{ fill: "#484f58", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip key="tip" contentStyle={{ background: "#0d1117", border: "1px solid #21262d", borderRadius: 6, fontSize: 10 }} labelStyle={{ color: "#7d8590" }} itemStyle={{ color: "#e6edf3" }} />
              <Area key="errors" type="monotone" dataKey="errors" stroke="#f85149" strokeWidth={1.5} fill="url(#anom-grad-errors)" dot={false} />
              <Area key="anomalies" type="monotone" dataKey="anomalies" stroke="#d29922" strokeWidth={1.5} fill="url(#anom-grad-anomalies)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Service cards */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <AlertTriangle className="w-4 h-4 text-[#f85149]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Services - Anomaly Scores</span>
          <span className="ml-auto flex items-center gap-1 text-[#3fb950]" style={{ fontSize: "10px" }}>
            <Zap className="w-3 h-3" /> ML model v2.4 active
          </span>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {mockAnomalies.map((a) => <AnomalyCard key={a.id} anomaly={a} />)}
        </div>
      </div>
    </div>
  );
}

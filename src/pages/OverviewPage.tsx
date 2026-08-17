import React, { Suspense, useState } from "react";
import { MetricCards } from "../components/dashboard/MetricCards";
import { TrafficChart } from "../components/dashboard/TrafficChart";
import { ServiceHealthCards } from "../components/dashboard/ServiceHealthCards";
import { useLiveLogs } from "../hooks/useLiveLogs";
import { AnomalyDrawer } from "../components/dashboard/AnomalyDrawer";
import { Activity, AlertTriangle, ArrowRight, CheckCircle, Clock, Database, Eye, EyeOff } from "lucide-react";
import { useNavigate } from "react-router";
import { EmptyState } from "../components/common/EmptyState";
import { useTelemetryStream } from "../hooks/useTelemetryStream";

const BenchmarkingHUD = import.meta.env.VITE_ENABLE_BENCHMARKING === 'true'
  ? React.lazy(() => import("../components/dashboard/BenchmarkingHUD").then(m => ({ default: m.BenchmarkingHUD })))
  : () => null;

const SEVERITY_COLOR: Record<string, string> = {
  critical: "#f85149",
  high: "#ffa657",
  medium: "#d29922",
  low: "#7d8590",
};

function QuickStat({ label, value, colorClass }: { label: string; value: string; colorClass: string }) {
  return (
    <div className="flex flex-col gap-1 p-3 rounded-lg bg-[#0d1117] border border-[#21262d]">
      <span className="text-[#484f58] text-[10px]">{label}</span>
      <span className={`text-[20px] font-bold ${colorClass}`}>{value}</span>
    </div>
  );
}

export function OverviewPage() {
  const navigate = useNavigate();
  const { filteredLogs, totalLogCount, isBackfillLoading } = useLiveLogs();
  const { activeTrackingLoops } = useTelemetryStream();
  
  const [showLowSeverity, setShowLowSeverity] = useState(true);
  const [selectedIncident, setSelectedIncident] = useState<any | null>(null);
  
  const recentLogs = filteredLogs.slice(-6).reverse();
  
  // Filter active tracking loops based on severity toggle
  const visibleLoops = activeTrackingLoops.filter(loop => showLowSeverity || loop.severity !== "low");
  
  const openIncidents = visibleLoops.map((loop) => ({
    id: loop.window_id,
    service: loop.suspected_root_service || "unknown",
    timestamp: new Date().toLocaleTimeString(),
    description: `Anomaly detected with score ${loop.anomaly_score.toFixed(2)}`,
    ...loop,
  }));

  const totalErrors = filteredLogs.filter(l => l.level === 'ERROR' || l.level === 'FATAL' || l.level === 'CRITICAL').length;
  const avgErrorRate = filteredLogs.length > 0 ? ((totalErrors / filteredLogs.length) * 100).toFixed(1) : "0.0";
  const uptime = filteredLogs.length > 0 ? (100 - Number(avgErrorRate)).toFixed(1) : "100.0";
  
  const validLatencies = filteredLogs.map((l) => l.latency_ms).filter((l): l is number => l !== undefined);
  let p99Latency = "0";
  if (validLatencies.length >= 2) {
    validLatencies.sort((a, b) => a - b);
    p99Latency = validLatencies[Math.floor(validLatencies.length * 0.99)].toFixed(0);
  }

  if (totalLogCount === 0 && !isBackfillLoading) {
    return (
      <EmptyState
        title="No Telemetry Detected"
        description="We're waiting for the first logs to arrive. Ensure your services are configured to send telemetry to the ingestion gateway."
        icon={Database}
      />
    );
  }

  return (
    <div className="space-y-5 relative">
      {/* Top Global Filter Bar & Live Badge */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <h1 className="text-[#e6edf3] text-xl font-bold">Observability Overview</h1>
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-[#3fb950]/10 border border-[#3fb950]/20">
            <span className="w-2 h-2 rounded-full bg-[#3fb950] animate-pulse" />
            <span className="text-[#3fb950] text-[10px] font-bold tracking-wide uppercase">Telemetry Pipeline: Live</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowLowSeverity(!showLowSeverity)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
              showLowSeverity 
                ? "bg-[#161b22] border-[#21262d] text-[#c9d1d9] hover:border-[#8b949e]" 
                : "bg-[#388bfd]/10 border-[#388bfd]/30 text-[#388bfd] hover:bg-[#388bfd]/20"
            }`}
          >
            {showLowSeverity ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
            {showLowSeverity ? "Hide Low Severity" : "Show All Severities"}
          </button>
        </div>
      </div>

      {/* Metric cards */}
      <MetricCards showLowSeverity={showLowSeverity} onToggleLowSeverity={() => setShowLowSeverity(!showLowSeverity)} />

      {/* Quick stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <QuickStat label="Logs Ingested" value={totalLogCount.toLocaleString()} colorClass="text-[#e6edf3]" />
        <QuickStat label="Live P99 Latency" value={`${p99Latency}ms`} colorClass="text-[#f85149]" />
        <QuickStat label="Error rate" value={`${avgErrorRate}%`} colorClass="text-[#d29922]" />
        <QuickStat label="Session Uptime" value={`${uptime}%`} colorClass="text-[#3fb950]" />
      </div>

      {/* Sleek Dark Observability Traffic Chart */}
      <TrafficChart />

      {/* System Health & Service Status Matrix Cards */}
      <ServiceHealthCards />

      {/* Benchmarking HUD (if enabled) */}
      <Suspense fallback={null}>
        <BenchmarkingHUD />
      </Suspense>

      {/* Bottom row: recent logs + open incidents */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Recent log activity */}
        <div className="rounded-xl bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#21262d] overflow-hidden shadow-sm dark:shadow-none flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-[#21262d]">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-[#388bfd]" />
              <span className="text-slate-900 dark:text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Recent Activity</span>
            </div>
            <button
              onClick={() => navigate("/logs")}
              className="flex items-center gap-1 text-[#388bfd] hover:text-[#79c0ff] transition-colors"
              style={{ fontSize: "11px" }}
            >
              View all logs <ArrowRight className="w-3 h-3" />
            </button>
          </div>
          <div className="divide-y divide-slate-100 dark:divide-[#21262d] flex-1 overflow-y-auto max-h-[300px]">
            {recentLogs.map((log) => {
              const colors: Record<string, string> = { INFO: "#79c0ff", WARN: "#d29922", ERROR: "#f85149", FATAL: "#f85149", CRITICAL: "#f85149", DEBUG: "#7d8590" };
              return (
                <div key={log.id} className="flex items-start gap-3 px-4 py-2.5">
                  <span
                    className="shrink-0 mt-0.5"
                    style={{ fontSize: "9px", fontWeight: 700, fontFamily: "monospace", color: colors[log.level] || "#79c0ff", minWidth: 36 }}
                  >
                    {log.level}
                  </span>
                  <span className="text-slate-500 dark:text-[#484f58] shrink-0 mt-0.5" style={{ fontSize: "10px", fontFamily: "monospace" }}>
                    {log.timestamp}
                  </span>
                  <span className="text-slate-600 dark:text-[#7d8590] truncate flex-1" style={{ fontSize: "11px", fontFamily: "monospace" }}>
                    {log.message}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Open incidents */}
        <div className="rounded-xl bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#21262d] overflow-hidden shadow-sm dark:shadow-none flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-[#21262d]">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-[#ffa657]" />
              <span className="text-slate-900 dark:text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Open Incidents</span>
              <span className="px-1.5 py-0.5 rounded-full bg-[#da3633] text-white" style={{ fontSize: "10px", fontWeight: 700 }}>
                {openIncidents.length}
              </span>
            </div>
            <button
              onClick={() => navigate("/incidents")}
              className="flex items-center gap-1 text-[#388bfd] hover:text-[#79c0ff] transition-colors"
              style={{ fontSize: "11px" }}
            >
              View all <ArrowRight className="w-3 h-3" />
            </button>
          </div>
          <div className="divide-y divide-slate-100 dark:divide-[#21262d] flex-1 overflow-y-auto max-h-[300px]">
            {openIncidents.map((incident) => (
              <div 
                key={incident.id} 
                className="flex items-start gap-3 px-4 py-3 hover:bg-[#21262d]/50 cursor-pointer transition-colors"
                onClick={() => setSelectedIncident(incident)}
              >
                <span
                  className="w-2 h-2 rounded-full shrink-0 mt-1.5"
                  style={{ background: SEVERITY_COLOR[incident.severity] || "#f85149" }}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-slate-900 dark:text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 600 }}>{incident.service}</span>
                    <span className="text-slate-500 dark:text-[#484f58]" style={{ fontSize: "10px" }}>
                      <Clock className="w-2.5 h-2.5 inline mr-0.5" />{incident.timestamp}
                    </span>
                  </div>
                  <p className="text-[#7d8590] mt-0.5 truncate" style={{ fontSize: "10px" }}>{incident.description}</p>
                </div>
              </div>
            ))}
            {openIncidents.length === 0 && (
              <div className="flex items-center justify-center gap-2 py-8 text-[#3fb950]">
                <CheckCircle className="w-4 h-4" />
                <span style={{ fontSize: "12px" }}>All systems nominal</span>
              </div>
            )}
          </div>
        </div>
      </div>

      <AnomalyDrawer 
        isOpen={!!selectedIncident} 
        onClose={() => setSelectedIncident(null)} 
        incident={selectedIncident} 
      />
    </div>
  );
}

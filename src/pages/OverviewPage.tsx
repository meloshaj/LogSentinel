import React, { Suspense, useState } from "react";
import { MetricCards } from "../components/dashboard/MetricCards";
import { TrafficChart } from "../components/dashboard/TrafficChart";
import { ServiceHealthCards } from "../components/dashboard/ServiceHealthCards";
import { useLiveLogs } from "../hooks/useLiveLogs";
import { AnomalyDrawer } from "../components/dashboard/AnomalyDrawer";
import { Activity, AlertTriangle, ArrowRight, CheckCircle, Clock, Eye, EyeOff } from "lucide-react";
import { useNavigate } from "react-router";
import { useTelemetryStream } from "../hooks/useTelemetryStream";
import { AddDataSourceButton } from "../components/integrations/DataSourceModal";
import { resolveRootService } from "../utils/incident";
import { displayOperationalStatus, OperationalStateNotice } from "../components/common/OperationalState";
import type { Incident } from "../types/monitoring";
import type { TrackingLoopEvent } from "../providers/TelemetryProvider";

const BenchmarkingHUD = import.meta.env.VITE_ENABLE_BENCHMARKING === 'true'
  ? React.lazy(() => import("../components/dashboard/BenchmarkingHUD").then(m => ({ default: m.BenchmarkingHUD })))
  : () => null;

const SEVERITY_COLOR: Record<string, string> = {
  critical: "#f85149",
  high: "#ffa657",
  medium: "#d29922",
  low: "#7d8590",
};

type OverviewIncident = Incident & Omit<Partial<TrackingLoopEvent>, "id" | "status"> & { backendId?: number };

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
  const { filteredLogs, totalLogCount, isBackfillLoading, dataState: logState, connectionState } = useLiveLogs();
  const { activeTrackingLoops, trackingLoopsDataState } = useTelemetryStream();
  const logStatus = displayOperationalStatus(logState, connectionState);
  const loopStatus = displayOperationalStatus(trackingLoopsDataState, connectionState);
  const logsKnown = logStatus === "available" || logStatus === "empty" || logStatus === "stale";
  
  const [showLowSeverity, setShowLowSeverity] = useState(true);
  const [selectedIncident, setSelectedIncident] = useState<OverviewIncident | null>(null);
  
  const recentLogs = filteredLogs.slice(-6).reverse();
  
  // Filter active tracking loops based on severity toggle
  const visibleLoops = activeTrackingLoops.filter(loop => showLowSeverity || loop.severity !== "low");
  
  const openIncidents: OverviewIncident[] = visibleLoops.map((loop) => ({
    ...loop,
    id: loop.window_id,
    service: resolveRootService(loop) || "Root cause unavailable",
    timestamp: loop.created_at ? new Date(loop.created_at).toLocaleTimeString() : "Time unavailable",
    description: `Anomaly detected with score ${loop.anomaly_score.toFixed(2)}`,
    severity: (loop.severity === "critical" || loop.severity === "high" || loop.severity === "medium" || loop.severity === "low" ? loop.severity : "medium"),
    status: loop.status === "resolved" ? "resolved" : loop.status === "acknowledged" ? "acknowledged" : loop.status === "investigating" ? "investigating" : "open",
    backendId: loop.id,
  }));

  const totalErrors = filteredLogs.filter(l => l.level === 'ERROR' || l.level === 'FATAL' || l.level === 'CRITICAL').length;
  const avgErrorRate = filteredLogs.length > 0 ? ((totalErrors / filteredLogs.length) * 100).toFixed(1) : "No data";
  const uptime = filteredLogs.length > 0 ? (100 - Number(avgErrorRate)).toFixed(1) : "No data";
  
  const validLatencies = filteredLogs.map((l) => l.latency_ms).filter((l): l is number => l !== undefined);
  let p99Latency = "No data";
  if (validLatencies.length >= 2) {
    validLatencies.sort((a, b) => a - b);
    p99Latency = validLatencies[Math.floor(validLatencies.length * 0.99)].toFixed(0);
  }

  if (isBackfillLoading && totalLogCount === 0) {
    return (
      <OperationalStateNotice
        state={logState}
        connectionState={connectionState}
        emptyMessage="No telemetry has been returned yet."
        errorMessage="Dashboard telemetry is unavailable while the current snapshot loads."
        className="m-6"
      />
    );
  }

  return (
    <div className="space-y-5 relative">
      {logStatus !== "available" && (
        <OperationalStateNotice
          state={logState}
          connectionState={connectionState}
          emptyMessage="No telemetry was returned for this scope. A valid zero count remains visible below."
          errorMessage="Dashboard telemetry is unavailable; operational values are not being estimated."
        />
      )}
      {logStatus === "available" && loopStatus !== "available" && (
        <OperationalStateNotice
          state={trackingLoopsDataState}
          connectionState={connectionState}
          emptyMessage="No anomaly records were returned for this scope."
          errorMessage="Anomaly telemetry is unavailable; incident counts are not being estimated."
        />
      )}
      {/* Top Global Filter Bar & Live Badge */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <h1 className="text-[#e6edf3] text-xl font-bold">Observability Overview</h1>
          <div className={`flex items-center gap-2 rounded-full px-2.5 py-1 ${connectionState === "live" && logStatus === "available" ? "border border-[#3fb950]/20 bg-[#3fb950]/10 text-[#3fb950]" : "border border-[#d29922]/20 bg-[#d29922]/10 text-[#d29922]"}`}>
            <span className="h-2 w-2 rounded-full bg-current" />
            <span className="text-[10px] font-bold uppercase tracking-wide">Telemetry: {connectionState === "live" ? (logStatus === "stale" ? "Stale" : "Current") : connectionState}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <AddDataSourceButton variant="compact" />
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
        <QuickStat label="Logs Ingested" value={logsKnown ? totalLogCount.toLocaleString() : logStatus === "loading" ? "Loading" : "Unavailable"} colorClass="text-[#e6edf3]" />
        <QuickStat label="Live P99 Latency" value={p99Latency === "No data" ? p99Latency : `${p99Latency}ms`} colorClass="text-[#f85149]" />
        <QuickStat label="Error rate" value={avgErrorRate === "No data" ? avgErrorRate : `${avgErrorRate}%`} colorClass="text-[#d29922]" />
        <QuickStat label="Session Uptime" value={uptime === "No data" ? uptime : `${uptime}%`} colorClass="text-[#3fb950]" />
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
            {recentLogs.length === 0 && (
              <OperationalStateNotice
                state={logState}
                connectionState={connectionState}
                emptyMessage="No recent log records were returned."
                errorMessage="Recent log activity is unavailable."
                className="m-3"
              />
            )}
          </div>
        </div>

        {/* Open incidents */}
        <div className="rounded-xl bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#21262d] overflow-hidden shadow-sm dark:shadow-none flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-[#21262d]">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-[#ffa657]" />
              <span className="text-slate-900 dark:text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Open Incidents</span>
              <span className="px-1.5 py-0.5 rounded-full bg-[#da3633] text-white" style={{ fontSize: "10px", fontWeight: 700 }}>
                {loopStatus === "available" || loopStatus === "empty" || loopStatus === "stale" ? openIncidents.length : "—"}
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
              <button
                type="button"
                key={incident.id} 
                className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-[#21262d]/50"
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
              </button>
            ))}
            {openIncidents.length === 0 && loopStatus === "empty" && (
              <div className="flex items-center justify-center gap-2 py-8 text-[#3fb950]">
                <CheckCircle className="w-4 h-4" />
                <span style={{ fontSize: "12px" }}>No current incident records</span>
              </div>
            )}
            {openIncidents.length === 0 && loopStatus !== "empty" && (
              <OperationalStateNotice
                state={trackingLoopsDataState}
                connectionState={connectionState}
                emptyMessage="No current incident records were returned."
                errorMessage="Incident data is unavailable."
                className="m-3"
              />
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

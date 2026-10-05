import type { ServiceAnomaly } from "../types/monitoring";
import { useTelemetryStream } from "../hooks/useTelemetryStream";
import { useLiveLogs } from "../hooks/useLiveLogs";
import { useState, useMemo } from "react";
import { AlertTriangle, ChevronRight, ShieldCheck, TrendingUp, Zap } from "lucide-react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { EmptyState } from "../components/common/EmptyState";
import { AnomalyDrawer } from "../components/dashboard/AnomalyDrawer";
import { displayOperationalStatus, OperationalStateNotice } from "../components/common/OperationalState";
import { buildServiceMetrics, deriveAnomalyRecords } from "../utils/anomalyRecords";

const STATUS_CONFIG = {
  Critical: { color: "#ef4444", bg: "bg-[#ef4444]/15", border: "border-[#ef4444]/30", dot: "bg-[#ef4444]", label: "text-[#ef4444]" },
  Warning: { color: "#f59e0b", bg: "bg-[#f59e0b]/15", border: "border-[#f59e0b]/25", dot: "bg-[#f59e0b]", label: "text-[#f59e0b]" },
  Low: { color: "#7d8590", bg: "bg-[#7d8590]/10", border: "border-[#7d8590]/20", dot: "bg-[#7d8590]", label: "text-[#c9d1d9]" },
} satisfies Record<ServiceAnomaly["status"], { color: string; bg: string; border: string; dot: string; label: string }>;

function ScoreBar({ score, status }: { score: number; status: ServiceAnomaly["status"] }) {
  const cfg = STATUS_CONFIG[status];
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[#21262d]">
        <div className="h-full rounded-full" style={{ width: `${score * 100}%`, background: cfg.color }} />
      </div>
      <span style={{ fontSize: "11px", fontWeight: 700, color: cfg.color, minWidth: 36, textAlign: "right" }}>{(score * 100).toFixed(0)}%</span>
    </div>
  );
}

function AnomalyCard({ anomaly }: { anomaly: ServiceAnomaly }) {
  const cfg = STATUS_CONFIG[anomaly.status];
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const severity = anomaly.status === "Critical" ? "critical" : anomaly.status === "Warning" ? "medium" : "low";

  return (
    <>
      <button
        type="button"
        onClick={() => setIsDrawerOpen(true)}
        aria-label={`Inspect anomaly for ${anomaly.name}`}
        className={`flex cursor-pointer flex-col justify-between rounded-xl border p-4 text-left shadow-sm transition-all hover:border-[#8b949e] ${cfg.bg} ${cfg.border}`}
      >
        <div>
          <div className="mb-2 flex items-start justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 shrink-0 rounded-full ${cfg.dot}`} />
              <span className="font-mono text-xs font-bold text-[#e6edf3]">{anomaly.name}</span>
            </div>
            <span className={`rounded border px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider ${cfg.bg} ${cfg.label} ${cfg.border}`}>{anomaly.status}</span>
          </div>
          <ScoreBar score={anomaly.score} status={anomaly.status} />
          <p className="mt-2 text-xs leading-relaxed text-[#8b949e]">{anomaly.explanation}</p>
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-[#21262d]/60 pt-2.5 text-xs">
          <div className="flex gap-4">
            <span className="text-[#7d8590]">Error Rate: <span className="font-mono font-bold text-[#c9d1d9]">{anomaly.errorRate === null ? "Unavailable" : `${anomaly.errorRate}%`}</span></span>
            <span className="text-[#7d8590]">Latency: <span className="font-mono font-bold text-[#c9d1d9]">{anomaly.latency === null ? "Unavailable" : `${anomaly.latency}ms`}</span></span>
          </div>
          <ChevronRight className="h-3.5 w-3.5 text-[#7d8590]" />
        </div>
      </button>
      <AnomalyDrawer
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        incident={{
          id: anomaly.id,
          service: anomaly.name,
          severity,
          timestamp: anomaly.detectedAt ? new Date(anomaly.detectedAt).toLocaleTimeString() : "Time unavailable",
          description: anomaly.explanation,
          anomaly_score: anomaly.score,
          status: "open",
        }}
      />
    </>
  );
}

type TimeSeriesPoint = { time: string; errors: number; anomalies: number };

export function AnomaliesPage() {
  const { activeTrackingLoops, trackingLoopsDataState } = useTelemetryStream();
  const { filteredLogs, dataState: logDataState, connectionState } = useLiveLogs();
  const loopStatus = displayOperationalStatus(trackingLoopsDataState, connectionState);
  const logStatus = displayOperationalStatus(logDataState, connectionState);
  const serviceMetrics = useMemo(() => buildServiceMetrics(filteredLogs), [filteredLogs]);

  const timeSeriesData = useMemo<TimeSeriesPoint[]>(() => {
    const buckets = new Map<string, TimeSeriesPoint>();
    filteredLogs.forEach((log) => {
      const timestamp = new Date(log.timestamp);
      if (!Number.isFinite(timestamp.getTime())) return;
      const time = timestamp.toTimeString().slice(0, 5);
      const bucket = buckets.get(time) ?? { time, errors: 0, anomalies: 0 };
      if (["ERROR", "FATAL", "CRITICAL"].includes(log.level)) bucket.errors += 1;
      buckets.set(time, bucket);
    });
    activeTrackingLoops.forEach((loop) => {
      if (!loop.created_at) return;
      const timestamp = new Date(loop.created_at);
      if (!Number.isFinite(timestamp.getTime())) return;
      const time = timestamp.toTimeString().slice(0, 5);
      const bucket = buckets.get(time) ?? { time, errors: 0, anomalies: 0 };
      bucket.anomalies += 1;
      buckets.set(time, bucket);
    });
    return Array.from(buckets.values()).sort((a, b) => a.time.localeCompare(b.time)).slice(-20);
  }, [activeTrackingLoops, filteredLogs]);

  const anomalies = useMemo(
    () => deriveAnomalyRecords(activeTrackingLoops, serviceMetrics),
    [activeTrackingLoops, serviceMetrics],
  );

  const critical = anomalies.filter((anomaly) => anomaly.status === "Critical");
  const warning = anomalies.filter((anomaly) => anomaly.status === "Warning");
  const low = anomalies.filter((anomaly) => anomaly.status === "Low");
  const hasKnownData = loopStatus === "available" || loopStatus === "empty" || loopStatus === "stale";

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Anomaly Detection</h1>
        <p className="mt-0.5 text-[#7d8590]" style={{ fontSize: "12px" }}>Backend tracking-loop state and graph blast-radius evidence</p>
      </div>

      <OperationalStateNotice
        state={trackingLoopsDataState}
        connectionState={connectionState}
        emptyMessage="No anomaly records were returned for this scope."
        errorMessage="Anomaly data is unavailable; the page is not treating that as zero anomalies."
      />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {[
          { label: "Critical Services", count: critical.length },
          { label: "Warning Services", count: warning.length },
          { label: "Low Severity Services", count: low.length },
        ].map((item) => (
          <div key={item.label} className="flex items-center justify-between rounded-xl border border-[#21262d] bg-[#161b22] p-4">
            <span className="text-xs text-[#8b949e]">{item.label}</span>
            <span className="font-mono text-2xl font-extrabold text-[#e6edf3]">{hasKnownData ? item.count : "—"}</span>
          </div>
        ))}
      </div>

      <div className="overflow-hidden rounded-xl border border-[#21262d] bg-[#161b22] shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#21262d] px-4 py-3">
          <div className="flex items-center gap-2"><TrendingUp className="h-4 w-4 text-[#ef4444]" /><span className="text-[13px] font-semibold text-[#e6edf3]">Anomaly Score &amp; Error Trend</span></div>
          <span className="text-[10px] text-[#7d8590]">Log source: {logStatus === "available" ? "current" : logStatus}</span>
        </div>
        {timeSeriesData.length > 0 ? (
          <div className="px-4 pb-4 pt-2">
            <ResponsiveContainer width="100%" height={165}>
              <AreaChart data={timeSeriesData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="anom-grad-anomalies" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#ef4444" stopOpacity={0.35} /><stop offset="95%" stopColor="#ef4444" stopOpacity={0.01} /></linearGradient>
                  <linearGradient id="anom-grad-errors" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#f59e0b" stopOpacity={0.25} /><stop offset="95%" stopColor="#f59e0b" stopOpacity={0.01} /></linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
                <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={{ stroke: "#30363d" }} tickLine={false} />
                <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} domain={[0, "auto"]} />
                <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #21262d", borderRadius: 8, fontSize: 11 }} />
                <Area type="monotone" dataKey="errors" name="Errors" stroke="#f59e0b" strokeWidth={1.8} fill="url(#anom-grad-errors)" dot={false} />
                <Area type="monotone" dataKey="anomalies" name="Anomalies" stroke="#ef4444" strokeWidth={2.2} fill="url(#anom-grad-anomalies)" dot={{ r: 2.5, fill: "#ef4444", strokeWidth: 0 }} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <OperationalStateNotice
            state={trackingLoopsDataState}
            connectionState={connectionState}
            emptyMessage="There are no anomaly or error points in the current successful response."
            errorMessage="The anomaly trend is unavailable."
            className="m-4"
          />
        )}
      </div>

      <div>
        <div className="mb-3 flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-[#ef4444]" /><span className="text-[13px] font-semibold text-[#e6edf3]">Services — Anomaly Scores</span><span className="ml-auto flex items-center gap-1 text-[10px] text-[#7d8590]"><Zap className="h-3 w-3" /> Backend result</span></div>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {anomalies.map((anomaly) => <AnomalyCard key={anomaly.id} anomaly={anomaly} />)}
          {anomalies.length === 0 && hasKnownData && (
            <div className="col-span-full"><EmptyState title="No Anomaly Records" description="The backend returned an empty anomaly set for this scope." icon={ShieldCheck} /></div>
          )}
          {anomalies.length === 0 && !hasKnownData && (
            <OperationalStateNotice state={trackingLoopsDataState} connectionState={connectionState} errorMessage="Anomaly records are unavailable." className="col-span-full" />
          )}
        </div>
      </div>
    </div>
  );
}

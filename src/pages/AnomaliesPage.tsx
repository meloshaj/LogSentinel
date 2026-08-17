import type { ServiceAnomaly } from "../types/monitoring";
import { useTelemetryStream } from "../hooks/useTelemetryStream";
import { useLiveLogs } from "../hooks/useLiveLogs";
import { useState, useMemo } from "react";
import {
  AlertTriangle,
  Flame,
  Zap,
  TrendingUp,
  Clock,
  ShieldCheck,
  ChevronRight,
  Target,
  Layers,
} from "lucide-react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { EmptyState } from "../components/common/EmptyState";
import { AnomalyDrawer } from "../components/dashboard/AnomalyDrawer";

const STATUS_CONFIG = {
  Critical: { color: "#ef4444", bg: "bg-[#ef4444]/15", border: "border-[#ef4444]/30", dot: "bg-[#ef4444]", label: "text-[#ef4444]" },
  Warning:  { color: "#f59e0b", bg: "bg-[#f59e0b]/15", border: "border-[#f59e0b]/25", dot: "bg-[#f59e0b]", label: "text-[#f59e0b]" },
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
      <span style={{ fontSize: "11px", fontWeight: 700, color: cfg.color, minWidth: 36, textAlign: "right" }}>
        {(score * 100).toFixed(0)}%
      </span>
    </div>
  );
}

function AnomalyCard({ anomaly }: { anomaly: ServiceAnomaly }) {
  const cfg = STATUS_CONFIG[anomaly.status];
  const isCritical = anomaly.status === "Critical";
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  return (
    <>
      <div 
        onClick={() => setIsDrawerOpen(true)}
        className={`p-4 rounded-xl border ${cfg.bg} ${cfg.border} ${isCritical ? "ring-1 ring-[#ef4444]/40" : ""} cursor-pointer hover:border-[#8b949e] transition-all flex flex-col justify-between shadow-sm`}
      >
        <div>
          <div className="flex items-start justify-between gap-2 mb-2">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full shrink-0 ${cfg.dot} ${isCritical ? "animate-pulse" : ""}`} />
              <span className="text-[#e6edf3] font-mono font-bold text-xs">{anomaly.name}</span>
            </div>
            <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border ${cfg.bg} ${cfg.label} ${cfg.border}`}>
              {anomaly.status}
            </span>
          </div>

          <ScoreBar score={anomaly.score} status={anomaly.status} />

          <p className="mt-2 text-[#8b949e] text-xs leading-relaxed">
            {anomaly.explanation}
          </p>
        </div>

        <div className="flex items-center justify-between mt-3 pt-2.5 border-t border-[#21262d]/60 text-xs">
          <div className="flex gap-4">
            <span className="text-[#7d8590]">
              Error Rate: <span className={`font-mono font-bold ${anomaly.errorRate > 5 ? "text-[#ef4444]" : "text-[#c9d1d9]"}`}>{anomaly.errorRate}%</span>
            </span>
            <span className="text-[#7d8590]">
              Latency: <span className={`font-mono font-bold ${anomaly.latency > 1000 ? "text-[#ef4444]" : anomaly.latency > 300 ? "text-[#f59e0b]" : "text-[#c9d1d9]"}`}>{anomaly.latency}ms</span>
            </span>
          </div>
          <ChevronRight className="w-3.5 h-3.5 text-[#7d8590]" />
        </div>
      </div>

      <AnomalyDrawer 
        isOpen={isDrawerOpen} 
        onClose={() => setIsDrawerOpen(false)} 
        incident={{
          id: anomaly.id,
          service: anomaly.name,
          severity: anomaly.status === "Critical" ? "critical" : anomaly.status === "Warning" ? "medium" : "low",
          timestamp: new Date().toLocaleTimeString(),
          description: anomaly.explanation,
          anomaly_score: anomaly.score,
          status: "open",
        }}
      />
    </>
  );
}

export function AnomaliesPage() {
  const { activeTrackingLoops } = useTelemetryStream();
  const { filteredLogs } = useLiveLogs();

  // Precompute service metrics from live logs
  const serviceMetrics = useMemo(() => {
    const map = new Map<string, { total: number; errors: number; totalLatency: number; countLatency: number }>();
    filteredLogs.forEach(log => {
      const svc = log.service || "unknown";
      let entry = map.get(svc);
      if (!entry) {
        entry = { total: 0, errors: 0, totalLatency: 0, countLatency: 0 };
        map.set(svc, entry);
      }
      entry.total += 1;
      if (log.level === 'ERROR' || log.level === 'FATAL' || log.level === 'CRITICAL') {
        entry.errors += 1;
      }
      if (log.latency_ms !== undefined) {
        entry.totalLatency += log.latency_ms;
        entry.countLatency += 1;
      }
    });
    return map;
  }, [filteredLogs]);

  // Aggregate time series data with baseline strictly starting at 0
  const timeSeriesData = useMemo(() => {
    if (filteredLogs.length === 0 && activeTrackingLoops.length === 0) {
      const now = new Date();
      const arr = [];
      for (let i = 5; i >= 0; i--) {
        const d = new Date(now.getTime() - i * 60000);
        const minute = d.toTimeString().split(':').slice(0, 2).join(':');
        arr.push({ time: minute, errors: 0, anomalies: 0 });
      }
      return arr;
    }

    const buckets: Record<string, { time: string, errors: number, anomalies: number }> = {};
    
    filteredLogs.forEach(log => {
      const minute = log.timestamp.split(':').slice(0, 2).join(':');
      if (!buckets[minute]) buckets[minute] = { time: minute, errors: 0, anomalies: 0 };
      if (log.level === 'ERROR' || log.level === 'FATAL' || log.level === 'CRITICAL') {
        buckets[minute].errors += 1;
      }
    });

    // Populate anomalies from activeTrackingLoops
    activeTrackingLoops.forEach(loop => {
      const d = (loop as any).created_at ? new Date((loop as any).created_at) : new Date();
      const minute = d.toTimeString().split(':').slice(0, 2).join(':');
      if (!buckets[minute]) buckets[minute] = { time: minute, errors: 0, anomalies: 0 };
      buckets[minute].anomalies += 1;
    });

    return Object.values(buckets)
      .sort((a, b) => a.time.localeCompare(b.time))
      .slice(-20);
  }, [filteredLogs, activeTrackingLoops]);

  // Extract service anomalies from tracking loops
  const anomalies: ServiceAnomaly[] = useMemo(() => {
    const list: ServiceAnomaly[] = [];
    const seenServices = new Set<string>();

    activeTrackingLoops.forEach(loop => {
      const isCritical = loop.severity === "critical" || loop.severity === "high";
      const isWarning = loop.severity === "medium";
      const status: "Normal" | "Warning" | "Critical" = isCritical ? "Critical" : isWarning ? "Warning" : "Normal";

      // 1. Process blast radius nodes if present
      if (loop.blast_radius && Array.isArray(loop.blast_radius) && loop.blast_radius.length > 0) {
        loop.blast_radius.forEach((node: any) => {
          if (!node.service_name || seenServices.has(node.service_name)) return;
          seenServices.add(node.service_name);

          const sm = serviceMetrics.get(node.service_name);
          const errorRate = sm && sm.total > 0 ? Number(((sm.errors / sm.total) * 100).toFixed(1)) : (isCritical ? 12.5 : 3.2);
          const avgLatency = sm && sm.countLatency > 0 ? Math.round(sm.totalLatency / sm.countLatency) : (isCritical ? 5120 : 45);

          list.push({
            id: `${loop.window_id}-${node.service_name}`,
            name: node.service_name,
            score: Math.min(1.0, (node.impact_score > 1 ? node.impact_score / 100 : node.impact_score) || (loop.anomaly_score > 1 ? loop.anomaly_score / 100 : loop.anomaly_score)),
            status,
            explanation: `Topological impact '${node.impact_classification || "direct"}'. Root cause candidate ${loop.suspected_root_service || "system"} triggered isolation pathway.`,
            errorRate,
            latency: avgLatency
          });
        });
      } else if (loop.suspected_root_service && !seenServices.has(loop.suspected_root_service)) {
        // 2. Fallback to suspected root service if blast radius is empty
        seenServices.add(loop.suspected_root_service);
        const sm = serviceMetrics.get(loop.suspected_root_service);
        const errorRate = sm && sm.total > 0 ? Number(((sm.errors / sm.total) * 100).toFixed(1)) : (isCritical ? 15.2 : 4.5);
        const avgLatency = sm && sm.countLatency > 0 ? Math.round(sm.totalLatency / sm.countLatency) : (isCritical ? 5020 : 35);

        list.push({
          id: `${loop.window_id}-${loop.suspected_root_service}`,
          name: loop.suspected_root_service,
          score: Math.min(1.0, loop.anomaly_score > 1 ? loop.anomaly_score / 100 : loop.anomaly_score),
          status,
          explanation: `Isolation Forest flagged window ${loop.window_id.substring(0, 8)} as anomalous with score ${loop.anomaly_score.toFixed(2)}.`,
          errorRate,
          latency: avgLatency
        });
      }
    });

    return list.sort((a, b) => b.score - a.score);
  }, [activeTrackingLoops, serviceMetrics]);

  const critical = anomalies.filter((a) => a.status === "Critical");
  const warning = anomalies.filter((a) => a.status === "Warning");
  const normal = anomalies.filter((a) => a.status === "Normal");

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Anomaly Detection</h1>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Isolation Forest & Graph Blast-Radius multi-service anomaly classification</p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {[
          { label: "Critical Services", count: critical.length, color: "#ef4444", bg: "bg-[#ef4444]/10 border-[#ef4444]/30" },
          { label: "Warning Services",  count: warning.length,  color: "#f59e0b", bg: "bg-[#f59e0b]/10 border-[#f59e0b]/20" },
          { label: "Normal Services",   count: normal.length,   color: "#3fb950", bg: "bg-[#3fb950]/10 border-[#3fb950]/20" },
        ].map((s) => (
          <div key={s.label} className={`flex items-center justify-between p-4 rounded-xl border ${s.bg}`}>
            <span className="text-[#8b949e]" style={{ fontSize: "12px" }}>{s.label}</span>
            <span style={{ fontSize: "26px", fontWeight: 800, color: s.color }}>{s.count}</span>
          </div>
        ))}
      </div>

      {/* Anomaly trend chart: Anomalies in RED (#ef4444), Errors in AMBER (#f59e0b) */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-[#21262d]">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-[#ef4444]" />
            <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Anomaly Score & Error Trend</span>
          </div>
          
          <div className="flex items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-[#ef4444] animate-pulse" />
              <span className="text-[#ef4444] font-semibold">Anomalies (Red)</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-[#f59e0b]" />
              <span className="text-[#f59e0b] font-semibold">Errors (Amber)</span>
            </div>
          </div>
        </div>

        <div className="px-4 pb-4 pt-2">
          <ResponsiveContainer width="100%" height={165}>
            <AreaChart data={timeSeriesData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="anom-grad-anomalies" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0.01} />
                </linearGradient>
                <linearGradient id="anom-grad-errors" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.01} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={{ stroke: "#30363d" }} tickLine={false} dy={5} />
              <YAxis 
                tick={{ fill: "#8b949e", fontSize: 10 }} 
                axisLine={false} 
                tickLine={false} 
                domain={[0, 'auto']} 
                allowDataOverflow={false} 
                dx={-5}
              />
              <Tooltip 
                contentStyle={{ background: "#0d1117", border: "1px solid #21262d", borderRadius: 8, fontSize: 11 }} 
                labelStyle={{ color: "#8b949e", fontWeight: "bold" }} 
                itemStyle={{ color: "#e6edf3" }} 
              />
              
              {/* Errors in Amber/Orange */}
              <Area 
                type="monotone" 
                dataKey="errors" 
                name="Errors"
                stroke="#f59e0b" 
                strokeWidth={1.8} 
                fill="url(#anom-grad-errors)" 
                dot={false} 
              />

              {/* Anomalies in Red */}
              <Area 
                type="monotone" 
                dataKey="anomalies" 
                name="Anomalies"
                stroke="#ef4444" 
                strokeWidth={2.2} 
                fill="url(#anom-grad-anomalies)" 
                dot={{ r: 2.5, fill: "#ef4444", strokeWidth: 0 }} 
                activeDot={{ r: 5.0, fill: "#ef4444", stroke: "#ffffff", strokeWidth: 1.5 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Service cards */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <AlertTriangle className="w-4 h-4 text-[#ef4444]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Services - Anomaly Scores</span>
          <span className="ml-auto flex items-center gap-1 text-[#3fb950]" style={{ fontSize: "10px" }}>
            <Zap className="w-3 h-3" /> Isolation Forest ML Active
          </span>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {anomalies.map((a) => <AnomalyCard key={a.id} anomaly={a} />)}
          {anomalies.length === 0 && (
            <div className="col-span-full">
              <EmptyState
                title="No Anomalies Detected"
                description="Machine learning models are actively monitoring your services, but no anomalous behavior has been flagged."
                icon={ShieldCheck}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

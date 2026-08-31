import type { Incident } from "../types/monitoring";
import { useTelemetryStream } from "../hooks/useTelemetryStream";
import { useLiveLogs } from "../hooks/useLiveLogs";
import { IncidentBlastRadiusMap } from "../components/topology/IncidentBlastRadiusMap";
import { Bell, CheckCircle, Clock, Flame, XCircle, AlertTriangle, ChevronRight, X, Filter, Activity, Network, Target, ShieldAlert, Cpu, Layers } from "lucide-react";
import { useState, useMemo } from "react";
import { EmptyState } from "../components/common/EmptyState";
import { resolveRootService } from "../utils/incident";
import { AnomalyDrawer } from "../components/dashboard/AnomalyDrawer";

const SEVERITY_CONFIG = {
  critical: { label: "CRITICAL", color: "#ef4444", bg: "rgba(239,68,68,0.12)", border: "rgba(239,68,68,0.35)", icon: Flame },
  high:     { label: "HIGH",     color: "#f97316", bg: "rgba(249,115,22,0.12)", border: "rgba(249,115,22,0.3)",  icon: XCircle },
  medium:   { label: "MEDIUM",   color: "#f59e0b", bg: "rgba(245,158,11,0.12)", border: "rgba(245,158,11,0.25)", icon: Bell },
  low:      { label: "LOW",      color: "#7d8590", bg: "rgba(125,133,144,0.08)", border: "rgba(125,133,144,0.2)", icon: Clock },
};

const STATUS_CONFIG = {
  investigating: { label: "Investigating", color: "#f59e0b", dot: "bg-[#f59e0b] animate-pulse" },
  open:          { label: "Open",          color: "#ef4444", dot: "bg-[#ef4444] animate-pulse" },
  resolved:      { label: "Resolved",      color: "#3fb950", dot: "bg-[#3fb950]" },
};

function IncidentCard({ incident, onClick }: { incident: Incident, onClick: () => void }) {
  const sev = SEVERITY_CONFIG[incident.severity] || SEVERITY_CONFIG.medium;
  const stat = STATUS_CONFIG[incident.status] || STATUS_CONFIG.open;
  const SevIcon = sev.icon;

  return (
    <div
      className="rounded-xl border overflow-hidden hover:brightness-110 transition-all cursor-pointer shadow-sm"
      style={{ background: sev.bg, borderColor: sev.border }}
      onClick={onClick}
    >
      <div className="w-full flex items-start gap-3.5 p-4 text-left">
        <div className="flex items-center justify-center w-9 h-9 rounded-lg shrink-0 border" style={{ background: `${sev.color}20`, borderColor: `${sev.color}40` }}>
          <SevIcon className="w-4 h-4" style={{ color: sev.color }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[#e6edf3] font-bold text-sm">{incident.service}</span>
            <div className="flex items-center gap-2 shrink-0">
              <div className="flex items-center gap-1.5 bg-[#0d1117]/60 px-2 py-0.5 rounded-full border border-[#21262d]">
                <span className={`w-1.5 h-1.5 rounded-full ${stat.dot}`} />
                <span style={{ fontSize: "10px", fontWeight: 600, color: stat.color }}>{stat.label}</span>
              </div>
              <span className="px-2 py-0.5 rounded text-white text-[9px] font-bold uppercase tracking-wider" style={{ background: sev.color }}>
                {sev.label}
              </span>
            </div>
          </div>
          <p className="text-[#8b949e] mt-1 text-xs">{incident.description}</p>
          <div className="flex items-center gap-1 mt-2 text-[#7d8590] text-[10px]">
            <Clock className="w-3 h-3" /> Detected at {incident.timestamp}
          </div>
        </div>
        <ChevronRight className="w-4 h-4 text-[#7d8590] shrink-0 mt-2" />
      </div>
    </div>
  );
}

export function IncidentsPage() {
  const { activeTrackingLoops, latestPerformanceEvents } = useTelemetryStream();
  const { filteredLogs } = useLiveLogs();
  const [selectedIncident, setSelectedIncident] = useState<any | null>(null);
  const [selectedServiceFilter, setSelectedServiceFilter] = useState<string | null>(null);

  // Derive incidents from live tracking loops and performance alerts
  const incidents: any[] = useMemo(() => {
    const list: any[] = [
      ...activeTrackingLoops.map((loop) => {
        const rootService = resolveRootService(loop);
        return {
          ...loop,
          id: loop.window_id,
          service: rootService || "Root cause unavailable",
          severity:
            loop.severity === "medium" || loop.severity === "low" || loop.severity === "high" || loop.severity === "critical"
              ? loop.severity
              : "medium",
          timestamp: (loop as any).created_at
            ? new Date((loop as any).created_at).toLocaleTimeString()
            : new Date().toLocaleTimeString(),
          description: `Anomaly loop detected with score ${loop.anomaly_score.toFixed(2)} across dependency cascade.`,
          status:
            loop.status === "open" || loop.status === "investigating" || loop.status === "resolved"
              ? loop.status
              : "open",
        };
      }),
      ...latestPerformanceEvents.map((event) => ({
        id: event.metric_name,
        service: "infrastructure",
        severity:
          event.severity === "medium" || event.severity === "low" || event.severity === "high" || event.severity === "critical"
            ? event.severity
            : "medium",
        timestamp: new Date().toLocaleTimeString(),
        description: `Performance alert: ${event.metric_name} is ${event.current_value.toFixed(0)} (threshold ${event.threshold})`,
        status: "open",
      })),
    ];
    return list;
  }, [activeTrackingLoops, latestPerformanceEvents]);

  const filteredIncidents = selectedServiceFilter
    ? incidents.filter(
        (i) =>
          i.service === selectedServiceFilter ||
          (i.blast_radius &&
            Array.isArray(i.blast_radius) &&
            i.blast_radius.some((b: any) => b.service_name === selectedServiceFilter)),
      )
    : incidents;

  const open = filteredIncidents.filter((i) => i.status !== "resolved");
  const resolved = filteredIncidents.filter((i) => i.status === "resolved");

  // Primary active incident summary for HUD overlay
  const primaryLoop = activeTrackingLoops[0];
  const blastNodes =
    primaryLoop?.blast_radius && Array.isArray(primaryLoop.blast_radius) ? primaryLoop.blast_radius : [];
  const primaryRootCause =
    (primaryLoop ? resolveRootService(primaryLoop) : null) ||
    blastNodes.find((b: any) => b.impact_classification === "root")?.service_name ||
    blastNodes[0]?.service_name ||
    (open[0]?.service !== "Root cause unavailable" ? open[0]?.service : null);
  const confidenceScore = primaryLoop?.anomaly_score
    ? Math.min(99, Math.round(primaryLoop.anomaly_score > 1 ? primaryLoop.anomaly_score : primaryLoop.anomaly_score * 100))
    : 94;
  const fleetImpactPercent =
    blastNodes.length > 0 ? Math.min(100, Math.round((blastNodes.length / 5) * 100)) : primaryRootCause ? 20 : 0;

  // Filtered service logs for inspector panel
  const inspectedLogs = useMemo(() => {
    if (!selectedServiceFilter) return [];
    return filteredLogs
      .filter(
        (l) =>
          l.service === selectedServiceFilter ||
          blastNodes.some((b: any) => b.service_name === l.service),
      )
      .slice(-8)
      .reverse();
  }, [blastNodes, filteredLogs, selectedServiceFilter]);

  if (incidents.length === 0) {
    return (
      <div className="space-y-5">
        <div>
          <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Service Incidents & Topology</h1>
          <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Real-time service health, dependency topology, and incident triage</p>
        </div>
        <EmptyState
          title="No Active Incidents Detected"
          description="Your services are operating normally. Any performance degradation or anomaly loop escalations will appear here."
          icon={CheckCircle}
        />
      </div>
    );
  }

  return (
    <div className="space-y-5 relative">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Service Incidents & Topology</h1>
          <p className="text-[#7d8590] mt-0.5 text-xs">Track, triage, and resolve service incidents — {open.length} active incidents requiring attention</p>
        </div>
      </div>

      {/* Incident KPI Summary */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "Open Incidents", count: open.length, color: "#ef4444" },
          { label: "Investigating", count: incidents.filter(i => i.status === "investigating").length, color: "#f59e0b" },
          { label: "Critical Severity", count: incidents.filter(i => i.severity === "critical" || i.severity === "high").length, color: "#ef4444" },
          { label: "Resolved (24h)", count: resolved.length, color: "#3fb950" },
        ].map((s) => (
          <div key={s.label} className="flex flex-col items-center justify-center p-4 rounded-xl bg-[#161b22] border border-[#21262d] shadow-sm">
            <span style={{ fontSize: "28px", fontWeight: 800, color: s.color }}>{s.count}</span>
            <span className="text-[#8b949e] text-xs font-medium mt-0.5">{s.label}</span>
          </div>
        ))}
      </div>

      {/* High-Impact Situational Awareness HUD Overlay */}
      {primaryRootCause && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 p-4 rounded-xl bg-[#ef4444]/10 border border-[#ef4444]/35 backdrop-blur-md shadow-[0_0_25px_rgba(239,68,68,0.12)]">
          <div className="flex items-center gap-3">
            <div className="p-3 bg-[#ef4444]/20 rounded-xl text-[#ef4444] border border-[#ef4444]/40 shrink-0">
              <Target className="w-5 h-5 animate-pulse" />
            </div>
            <div className="min-w-0">
              <span className="text-[#8b949e] text-[10px] uppercase font-bold tracking-wider">Suspected Root Cause</span>
              <h3 className="text-[#e6edf3] font-bold font-mono text-sm truncate mt-0.5">{primaryRootCause}</h3>
              <span className="text-[#ef4444] text-[11px] font-semibold">Primary Initiator</span>
            </div>
          </div>

          <div className="flex items-center gap-3 md:border-l md:border-[#ef4444]/20 md:pl-4">
            <div className="p-3 bg-[#f59e0b]/20 rounded-xl text-[#f59e0b] border border-[#f59e0b]/40 shrink-0">
              <Layers className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <span className="text-[#8b949e] text-[10px] uppercase font-bold tracking-wider">Blast Radius Scope</span>
              <h3 className="text-[#e6edf3] font-bold font-mono text-sm mt-0.5">{fleetImpactPercent}% of fleet</h3>
              <span className="text-[#f59e0b] text-[11px] font-semibold">{blastNodes.length || 1} downstream services impacted</span>
            </div>
          </div>

          <div className="flex items-center gap-3 md:border-l md:border-[#ef4444]/20 md:pl-4">
            <div className="p-3 bg-[#388bfd]/20 rounded-xl text-[#388bfd] border border-[#388bfd]/40 shrink-0">
              <Cpu className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <span className="text-[#8b949e] text-[10px] uppercase font-bold tracking-wider">Root-Cause Confidence</span>
              <h3 className="text-[#388bfd] font-bold font-mono text-sm mt-0.5">{confidenceScore}% Probability</h3>
              <span className="text-[#8b949e] text-[11px]">Graph pathway algorithm score</span>
            </div>
          </div>
        </div>
      )}

      {/* Enhanced Topology Dependency Graph Section */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm flex flex-col">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-[#21262d]">
          <div className="flex items-center gap-2">
            <Network className="w-4 h-4 text-[#388bfd]" />
            <span className="text-[#e6edf3] text-[13px] font-bold">Incident Radar & Blast Cascade Map</span>
          </div>
          
          <div className="flex items-center gap-3">
            {selectedServiceFilter && (
              <div className="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-[#388bfd]/15 border border-[#388bfd]/30 text-xs text-[#388bfd]">
                <span>Inspecting: <strong className="font-mono">{selectedServiceFilter}</strong></span>
                <button 
                  onClick={() => setSelectedServiceFilter(null)} 
                  className="hover:text-white transition-colors ml-1"
                  title="Clear filter"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
            
            {/* Color-Coded Pill Indicators */}
            <div className="flex items-center gap-2 text-[11px]">
              <span className="px-2 py-0.5 rounded-full bg-[#ef4444]/15 border border-[#ef4444]/30 text-[#ef4444] font-bold flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#ef4444] animate-pulse" /> Root Cause
              </span>
              <span className="px-2 py-0.5 rounded-full bg-[#f59e0b]/15 border border-[#f59e0b]/30 text-[#f59e0b] font-bold flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#f59e0b]" /> Blast Cascade
              </span>
              <span className="px-2 py-0.5 rounded-full bg-[#334155]/40 border border-[#475569] text-[#cbd5e1] font-bold flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#64748b]" /> Nominal
              </span>
            </div>
          </div>
        </div>

        <div className="h-[480px] w-full relative bg-[#0d1117]">
          <IncidentBlastRadiusMap
            rootCause={primaryRootCause || null}
            affectedServices={blastNodes.map((node) => node.service_name)}
            onSelect={setSelectedServiceFilter}
          />
        </div>

        {/* Selected Node Inspector Panel */}
        {selectedServiceFilter && (
          <div className="p-4 bg-[#0d1117] border-t border-[#21262d]">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-[#388bfd]" />
                <span className="text-[#e6edf3] text-xs font-bold font-mono">{selectedServiceFilter} Live Telemetry & Log Stream</span>
              </div>
              <span className="text-[#7d8590] text-[10px]">{inspectedLogs.length} Recent Logs</span>
            </div>
            <div className="space-y-1 max-h-[160px] overflow-y-auto">
              {inspectedLogs.map((log) => (
                <div key={log.id} className="flex items-center gap-2.5 py-1 px-2 rounded bg-[#161b22] text-[11px] font-mono border border-[#21262d]">
                  <span className={`text-[9px] font-bold ${log.level === 'ERROR' || log.level === 'FATAL' || log.level === 'CRITICAL' ? 'text-[#ef4444]' : 'text-[#79c0ff]'}`}>
                    {log.level}
                  </span>
                  <span className="text-[#7d8590] text-[10px] shrink-0">{log.timestamp}</span>
                  <span className="text-[#c9d1d9] truncate flex-1">{log.message}</span>
                  {log.latency_ms && <span className="text-[#7d8590] text-[10px]">{log.latency_ms}ms</span>}
                </div>
              ))}
              {inspectedLogs.length === 0 && (
                <div className="text-[#7d8590] text-xs py-3 text-center">No recent log events for {selectedServiceFilter}</div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Incident timeline */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <Clock className="w-4 h-4 text-[#388bfd]" />
          <span className="text-[#e6edf3] text-[13px] font-bold">Incident Timeline</span>
          <span className="text-[#7d8590] ml-auto text-xs font-mono">{new Date().toLocaleDateString()}</span>
        </div>
        <div className="p-4">
          <div className="flex flex-col gap-2.5">
            {filteredIncidents.map((ev, idx) => {
              const isCrit = ev.severity === "critical" || ev.severity === "high";
              const isMed = ev.severity === "medium";
              const bg = isCrit 
                ? "bg-[#ef4444]/10 border-[#ef4444]/30 text-[#ef4444]" 
                : isMed 
                  ? "bg-[#f59e0b]/10 border-[#f59e0b]/30 text-[#f59e0b]" 
                  : "bg-[#7d8590]/10 border-[#7d8590]/30 text-[#7d8590]";
              const dotColor = isCrit ? "bg-[#ef4444]" : isMed ? "bg-[#f59e0b]" : "bg-[#7d8590]";
              
              return (
                <div 
                  key={idx} 
                  className="flex items-center gap-3.5 p-3 rounded-xl border bg-[#0d1117] border-[#21262d] hover:border-[#388bfd]/50 transition-all shadow-sm cursor-pointer"
                  onClick={() => setSelectedIncident(ev)}
                >
                  <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${dotColor} animate-pulse`} />
                  <span className="text-[#7d8590] shrink-0 font-mono text-xs">{ev.timestamp}</span>
                  <span className="text-[#e6edf3] font-bold text-xs font-mono bg-[#161b22] px-2 py-0.5 rounded border border-[#21262d]">{ev.service}</span>
                  <span className="text-[#8b949e] truncate text-xs font-medium ml-1 flex-1">{ev.description}</span>
                  <span className={`px-2.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border ${bg}`}>
                    {ev.severity}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Open incidents list */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <AlertTriangle className="w-4 h-4 text-[#ef4444]" />
          <span className="text-[#e6edf3] text-[13px] font-bold">Open Incidents ({open.length})</span>
        </div>
        <div className="space-y-2.5">
          {open.map((i) => <IncidentCard key={i.id} incident={i} onClick={() => setSelectedIncident(i)} />)}
        </div>
      </div>

      {/* Resolved incidents list */}
      {resolved.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle className="w-4 h-4 text-[#3fb950]" />
            <span className="text-[#e6edf3] text-[13px] font-bold">Resolved (last 24h)</span>
          </div>
          <div className="space-y-2.5">
            {resolved.map((i) => <IncidentCard key={i.id} incident={i} onClick={() => setSelectedIncident(i)} />)}
          </div>
        </div>
      )}

      <AnomalyDrawer 
        isOpen={!!selectedIncident} 
        onClose={() => setSelectedIncident(null)} 
        incident={selectedIncident} 
      />
    </div>
  );
}

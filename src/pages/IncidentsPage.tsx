import type { Incident } from "../types/monitoring";
import { useTelemetryStream } from "../hooks/useTelemetryStream";
import { ServiceTopologyGraph } from "../components/topology/ServiceTopologyGraph";
import { Bell, CheckCircle, Clock, Flame, XCircle, AlertTriangle, ChevronRight, X } from "lucide-react";
import { useState } from "react";
import { EmptyState } from "../components/common/EmptyState";
import { AnomalyDrawer } from "../components/dashboard/AnomalyDrawer";

const SEVERITY_CONFIG = {
  critical: { label: "CRITICAL", color: "#f85149", bg: "rgba(218,54,51,0.12)", border: "rgba(218,54,51,0.35)", icon: Flame },
  high:     { label: "HIGH",     color: "#ffa657", bg: "rgba(255,166,87,0.12)", border: "rgba(255,166,87,0.3)",  icon: XCircle },
  medium:   { label: "MEDIUM",   color: "#d29922", bg: "rgba(210,153,34,0.12)", border: "rgba(210,153,34,0.25)", icon: Bell },
  low:      { label: "LOW",      color: "#7d8590", bg: "rgba(125,133,144,0.08)", border: "rgba(125,133,144,0.2)", icon: Clock },
};

const STATUS_CONFIG = {
  investigating: { label: "Investigating", color: "#d29922", dot: "bg-[#d29922] animate-pulse" },
  open:          { label: "Open",          color: "#f85149", dot: "bg-[#f85149] animate-pulse" },
  resolved:      { label: "Resolved",      color: "#3fb950", dot: "bg-[#3fb950]" },
};

function IncidentCard({ incident, onClick }: { incident: Incident, onClick: () => void }) {
  const sev = SEVERITY_CONFIG[incident.severity];
  const stat = STATUS_CONFIG[incident.status];
  const SevIcon = sev.icon;

  return (
    <div
      className="rounded-xl border overflow-hidden hover:opacity-80 transition-opacity cursor-pointer"
      style={{ background: sev.bg, borderColor: sev.border }}
      onClick={onClick}
    >
      <div className="w-full flex items-start gap-3 p-4 text-left">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg shrink-0" style={{ background: `${sev.color}25` }}>
          <SevIcon className="w-4 h-4" style={{ color: sev.color }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 700 }}>{incident.service}</span>
            <div className="flex items-center gap-2 shrink-0">
              <div className="flex items-center gap-1.5">
                <span className={`w-1.5 h-1.5 rounded-full ${stat.dot}`} />
                <span style={{ fontSize: "10px", fontWeight: 500, color: stat.color }}>{stat.label}</span>
              </div>
              <span className="px-2 py-0.5 rounded text-white" style={{ fontSize: "9px", fontWeight: 700, background: sev.color }}>
                {sev.label}
              </span>
            </div>
          </div>
          <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "11px" }}>{incident.description}</p>
          <div className="flex items-center gap-1 mt-1.5 text-[#484f58]" style={{ fontSize: "10px" }}>
            <Clock className="w-3 h-3" /> Started at {incident.timestamp}
          </div>
        </div>
        <ChevronRight className="w-4 h-4 text-[#484f58] shrink-0 mt-2" />
      </div>
    </div>
  );
}

export function IncidentsPage() {
  const { activeTrackingLoops, latestPerformanceEvents } = useTelemetryStream();
  const [selectedIncident, setSelectedIncident] = useState<any | null>(null);

  // Derive incidents from live tracking loops and performance alerts
  const incidents: any[] = [
    ...activeTrackingLoops.map(loop => ({
      ...loop,
      id: loop.window_id,
      service: loop.suspected_root_service || "multiple-services",
      severity: (loop.severity === "medium" || loop.severity === "low" || loop.severity === "high" || loop.severity === "critical" ? loop.severity : "medium"),
      timestamp: new Date().toLocaleTimeString(),
      description: `Anomaly loop detected with score ${loop.anomaly_score.toFixed(2)}`,
      status: (loop.status === "open" || loop.status === "investigating" || loop.status === "resolved" ? loop.status : "open")
    })),
    ...latestPerformanceEvents.map(event => ({
      id: event.metric_name,
      service: "infrastructure",
      severity: (event.severity === "medium" || event.severity === "low" || event.severity === "high" || event.severity === "critical" ? event.severity : "medium"),
      timestamp: new Date().toLocaleTimeString(),
      description: `Performance alert: ${event.metric_name} is ${event.current_value.toFixed(0)} (threshold ${event.threshold})`,
      status: "open"
    }))
  ];

  const [selectedServiceFilter, setSelectedServiceFilter] = useState<string | null>(null);

  const filteredIncidents = selectedServiceFilter
    ? incidents.filter((i) => i.service === selectedServiceFilter)
    : incidents;

  const open = filteredIncidents.filter((i) => i.status !== "resolved");
  const resolved = filteredIncidents.filter((i) => i.status === "resolved");

  if (incidents.length === 0) {
    return (
      <div className="space-y-5">
        <div>
          <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Incidents</h1>
          <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Track, triage and resolve service incidents</p>
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
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Incidents</h1>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Track, triage and resolve service incidents - {open.length} open now</p>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: "Open", count: open.length, color: "#f85149" },
          { label: "Investigating", count: incidents.filter(i => i.status === "investigating").length, color: "#d29922" },
          { label: "Critical", count: incidents.filter(i => i.severity === "critical").length, color: "#f85149" },
          { label: "Resolved (24h)", count: resolved.length, color: "#3fb950" },
        ].map((s) => (
          <div key={s.label} className="flex flex-col items-center justify-center p-4 rounded-xl bg-[#161b22] border border-[#21262d]">
            <span style={{ fontSize: "28px", fontWeight: 800, color: s.color }}>{s.count}</span>
            <span className="text-[#7d8590]" style={{ fontSize: "11px" }}>{s.label}</span>
          </div>
        ))}
      </div>

      {/* Topology Dependency Graph */}
      <div className="rounded-xl overflow-hidden" style={{ height: 480 }}>
        <ServiceTopologyGraph
          mode="full"
          selectedNodeId={selectedServiceFilter}
          onNodeSelect={setSelectedServiceFilter}
        />
      </div>

      {/* Active service filter badge */}
      {selectedServiceFilter && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[#161b22] border border-[#388bfd]/30">
          <span className="text-[#388bfd]" style={{ fontSize: "11px", fontWeight: 600 }}>
            Filtering by: {selectedServiceFilter}
          </span>
          <button
            onClick={() => setSelectedServiceFilter(null)}
            className="text-[#7d8590] hover:text-[#e6edf3] transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Incident timeline */}
      <div className="rounded-xl bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#21262d] overflow-hidden shadow-sm dark:shadow-none">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-200 dark:border-[#21262d]">
          <Clock className="w-4 h-4 text-[#388bfd]" />
          <span className="text-slate-900 dark:text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Incident Timeline</span>
          <span className="text-slate-500 dark:text-[#484f58] ml-auto" style={{ fontSize: "10px" }}>Today - {new Date().toLocaleDateString()}</span>
        </div>
        <div className="p-4">
          <div className="flex flex-col gap-3">
            {incidents.map((ev, idx) => {
              const bg = ev.severity === "critical" || ev.severity === "high" ? "bg-red-50 dark:bg-[#f85149]/10 border-red-200 dark:border-[#f85149]/20 text-red-600 dark:text-[#f85149]" : ev.severity === "medium" ? "bg-orange-50 dark:bg-[#d29922]/10 border-orange-200 dark:border-[#d29922]/20 text-orange-600 dark:text-[#d29922]" : "bg-gray-50 dark:bg-[#7d8590]/10 border-gray-200 dark:border-[#7d8590]/20 text-gray-600 dark:text-[#7d8590]";
              const dot = ev.severity === "critical" || ev.severity === "high" ? "bg-[#f85149]" : ev.severity === "medium" ? "bg-[#d29922]" : "bg-[#7d8590]";
              const typeLabel = ev.severity;
              
              return (
                <div 
                  key={idx} 
                  className="flex items-center gap-3 p-3 rounded-lg border bg-slate-50 dark:bg-[#0d1117] border-slate-200 dark:border-[#21262d] hover:border-[#388bfd]/50 transition-all shadow-sm cursor-pointer"
                  onClick={() => setSelectedIncident(ev)}
                >
                  <div className={`w-2.5 h-2.5 rounded-full shrink-0 shadow-[0_0_8px_rgba(0,0,0,0.5)] dark:shadow-none ${dot}`} style={{ boxShadow: `0 0 10px ${dot.replace('bg-', '')}` }} />
                  <span className="text-slate-500 dark:text-[#484f58] shrink-0 font-medium" style={{ fontSize: "11px", fontFamily: "monospace" }}>{ev.timestamp}</span>
                  <span className="text-slate-700 dark:text-[#c9d1d9] truncate font-medium ml-1" style={{ fontSize: "13px" }}>{ev.description}</span>
                  <span className={`ml-auto px-2.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border ${bg}`}>
                    {typeLabel}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Open incidents */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <AlertTriangle className="w-4 h-4 text-[#f85149]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Open Incidents</span>
        </div>
        <div className="space-y-2">
          {open.map((i) => <IncidentCard key={i.id} incident={i} onClick={() => setSelectedIncident(i)} />)}
        </div>
      </div>

      {/* Resolved */}
      {resolved.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle className="w-4 h-4 text-[#3fb950]" />
            <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Resolved (last 24h)</span>
          </div>
          <div className="space-y-2">
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


import { mockIncidents } from "../services/mockMonitoringData";
import type { Incident } from "../types/monitoring";
import { Bell, CheckCircle, Clock, Flame, XCircle, AlertTriangle, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

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

const TIMELINE_EVENTS = [
  { time: "14:32:07", event: "PagerDuty alert fired - incident #INC-20445 created", type: "alert" },
  { time: "14:32:05", event: "database-service connection pool exhausted (200/200)", type: "error" },
  { time: "14:32:06", event: "payment-service circuit breaker opened", type: "error" },
  { time: "14:32:07", event: "api-gateway returning 503 to clients", type: "warn" },
  { time: "14:30:00", event: "Anomaly score crossed threshold (0.85) for database-service", type: "warn" },
  { time: "14:28:12", event: "Long-running transaction detected on transactions table", type: "warn" },
  { time: "14:15:00", event: "Notification service email queue backlog began growing", type: "info" },
];

function IncidentCard({ incident }: { incident: Incident }) {
  const [expanded, setExpanded] = useState(false);
  const sev = SEVERITY_CONFIG[incident.severity];
  const stat = STATUS_CONFIG[incident.status];
  const SevIcon = sev.icon;

  return (
    <div
      className="rounded-xl border overflow-hidden"
      style={{ background: sev.bg, borderColor: sev.border }}
    >
      <button
        className="w-full flex items-start gap-3 p-4 text-left"
        onClick={() => setExpanded((e) => !e)}
      >
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
        {expanded ? <ChevronDown className="w-4 h-4 text-[#484f58] shrink-0 mt-1" /> : <ChevronRight className="w-4 h-4 text-[#484f58] shrink-0 mt-1" />}
      </button>

      {expanded && (
        <div className="px-4 pb-4 border-t border-[#21262d]/40 pt-3 space-y-3">
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: "Severity", value: sev.label, color: sev.color },
              { label: "Status", value: stat.label, color: stat.color },
              { label: "Duration", value: "~5 min", color: "#7d8590" },
            ].map((m) => (
              <div key={m.label} className="p-2 rounded-lg bg-[#0d1117] border border-[#21262d] text-center">
                <div style={{ fontSize: "12px", fontWeight: 700, color: m.color }}>{m.value}</div>
                <div className="text-[#484f58]" style={{ fontSize: "9px" }}>{m.label}</div>
              </div>
            ))}
          </div>
          <div className="p-3 rounded-lg bg-[#0d1117] border border-[#21262d]">
            <span className="text-[#484f58]" style={{ fontSize: "10px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>Recommended Action</span>
            <p className="text-[#7d8590] mt-1" style={{ fontSize: "11px", lineHeight: 1.5 }}>
              Check the AI Analysis page for detailed root cause breakdown and step-by-step remediation instructions.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export function IncidentsPage() {
  const open = mockIncidents.filter((i) => i.status !== "resolved");
  const resolved = mockIncidents.filter((i) => i.status === "resolved");

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Incidents</h1>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Track, triage and resolve service incidents - {open.length} open now</p>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: "Open", count: open.length, color: "#f85149" },
          { label: "Investigating", count: mockIncidents.filter(i => i.status === "investigating").length, color: "#d29922" },
          { label: "Critical", count: mockIncidents.filter(i => i.severity === "critical").length, color: "#f85149" },
          { label: "Resolved (24h)", count: resolved.length, color: "#3fb950" },
        ].map((s) => (
          <div key={s.label} className="flex flex-col items-center justify-center p-4 rounded-xl bg-[#161b22] border border-[#21262d]">
            <span style={{ fontSize: "28px", fontWeight: 800, color: s.color }}>{s.count}</span>
            <span className="text-[#7d8590]" style={{ fontSize: "11px" }}>{s.label}</span>
          </div>
        ))}
      </div>

      {/* Incident timeline */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <Clock className="w-4 h-4 text-[#388bfd]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Incident Timeline</span>
          <span className="text-[#484f58] ml-auto" style={{ fontSize: "10px" }}>Today - 14:00-14:33</span>
        </div>
        <div className="p-4">
          <div className="relative">
            {/* Vertical line */}
            <div className="absolute left-[11px] top-0 bottom-0 w-px bg-[#21262d]" />
            <div className="space-y-3">
              {TIMELINE_EVENTS.map((ev, idx) => {
                const dot = ev.type === "error" ? "#f85149" : ev.type === "warn" ? "#d29922" : ev.type === "alert" ? "#bc8cff" : "#7d8590";
                return (
                  <div key={idx} className="flex items-start gap-3 pl-1">
                    <span className="w-5 h-5 rounded-full border-2 border-[#0d1117] shrink-0 mt-0.5" style={{ background: dot, minWidth: 20 }} />
                    <div>
                      <span className="text-[#484f58] mr-2" style={{ fontSize: "10px", fontFamily: "monospace" }}>{ev.time}</span>
                      <span className="text-[#c9d1d9]" style={{ fontSize: "11px" }}>{ev.event}</span>
                    </div>
                  </div>
                );
              })}
            </div>
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
          {open.map((i) => <IncidentCard key={i.id} incident={i} />)}
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
            {resolved.map((i) => <IncidentCard key={i.id} incident={i} />)}
          </div>
        </div>
      )}
    </div>
  );
}

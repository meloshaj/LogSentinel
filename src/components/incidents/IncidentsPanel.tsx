import { mockIncidents } from "../../services/mockMonitoringData";
import type { Incident } from "../../types/monitoring";
import { Bell, CheckCircle, Clock, Flame, XCircle } from "lucide-react";

const SEVERITY_CONFIG = {
  critical: { label: "CRITICAL", color: "#f85149", bg: "bg-[#da3633]/15", border: "border-[#da3633]/30", icon: Flame },
  high:     { label: "HIGH",     color: "#ffa657", bg: "bg-[#ffa657]/15", border: "border-[#ffa657]/30", icon: XCircle },
  medium:   { label: "MEDIUM",   color: "#d29922", bg: "bg-[#d29922]/15", border: "border-[#d29922]/20", icon: Bell },
  low:      { label: "LOW",      color: "#7d8590", bg: "bg-[#21262d]/60", border: "border-[#21262d]",    icon: Clock },
};

const STATUS_CONFIG = {
  investigating: { label: "Investigating", color: "#d29922", dot: "bg-[#d29922] animate-pulse" },
  open:          { label: "Open",          color: "#f85149", dot: "bg-[#f85149] animate-pulse" },
  resolved:      { label: "Resolved",      color: "#3fb950", dot: "bg-[#3fb950]" },
};

function IncidentRow({ incident }: { incident: Incident }) {
  const sev = SEVERITY_CONFIG[incident.severity];
  const stat = STATUS_CONFIG[incident.status];
  const SevIcon = sev.icon;

  return (
    <div className={`flex items-start gap-3 p-3 rounded-lg border ${sev.bg} ${sev.border} hover:brightness-110 transition-all`}>
      <div className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0 mt-0.5" style={{ background: `${sev.color}20` }}>
        <SevIcon className="w-3.5 h-3.5" style={{ color: sev.color }} />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[#e6edf3] truncate" style={{ fontSize: "12px", fontWeight: 600 }}>{incident.service}</span>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className={`w-1.5 h-1.5 rounded-full ${stat.dot}`} />
            <span style={{ fontSize: "10px", fontWeight: 500, color: stat.color }}>{stat.label}</span>
          </div>
        </div>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "10px", lineHeight: 1.4 }}>{incident.description}</p>
        <div className="flex items-center gap-2 mt-1.5">
          <span
            className={`px-1.5 py-0.5 rounded text-white`}
            style={{ fontSize: "9px", fontWeight: 700, background: sev.color }}
          >
            {sev.label}
          </span>
          <span className="text-[#484f58]" style={{ fontSize: "10px" }}>
            <Clock className="w-2.5 h-2.5 inline mr-0.5" />
            {incident.timestamp}
          </span>
        </div>
      </div>
    </div>
  );
}

export function IncidentsPanel() {
  const openCount = mockIncidents.filter((i) => i.status !== "resolved").length;

  return (
    <div className="flex flex-col rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] shrink-0">
        <div className="flex items-center gap-2">
          <Bell className="w-4 h-4 text-[#ffa657]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Incidents & Alerts</span>
          {openCount > 0 && (
            <span className="px-1.5 py-0.5 rounded-full bg-[#da3633] text-white" style={{ fontSize: "10px", fontWeight: 700 }}>
              {openCount} open
            </span>
          )}
        </div>
        <button className="flex items-center gap-1 text-[#388bfd] hover:text-[#79c0ff] transition-colors" style={{ fontSize: "10px" }}>
          <CheckCircle className="w-3 h-3" />
          View all
        </button>
      </div>

      {/* Summary row */}
      <div className="grid grid-cols-3 divide-x divide-[#21262d] border-b border-[#21262d]">
        {[
          { label: "Critical", count: mockIncidents.filter(i => i.severity === "critical").length, color: "#f85149" },
          { label: "Investigating", count: mockIncidents.filter(i => i.status === "investigating").length, color: "#d29922" },
          { label: "Resolved (24h)", count: mockIncidents.filter(i => i.status === "resolved").length, color: "#3fb950" },
        ].map((item) => (
          <div key={item.label} className="flex flex-col items-center py-2">
            <span style={{ fontSize: "18px", fontWeight: 700, color: item.color }}>{item.count}</span>
            <span className="text-[#484f58]" style={{ fontSize: "9px" }}>{item.label}</span>
          </div>
        ))}
      </div>

      {/* Incident list */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
        {mockIncidents.map((incident) => (
          <IncidentRow key={incident.id} incident={incident} />
        ))}
      </div>
    </div>
  );
}

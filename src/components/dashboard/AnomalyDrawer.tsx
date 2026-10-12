import { X, Flame, Bell, Clock, Activity, ShieldAlert, XCircle, AlertTriangle, UserPlus, CheckCircle, ListChecks } from "lucide-react";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BlastRadiusNode, TrackingLoopEvent } from "../../providers/TelemetryProvider";
import { Skeleton } from "../common/Skeleton";
import { useDialogFocus } from "../../hooks/useDialogFocus";
import type { Incident } from "../../types/monitoring";
import { fetchAuthenticated } from "../../utils/auth";
import { useNavigate } from "react-router";

type IncidentDetails = Incident & Omit<Partial<TrackingLoopEvent>, "id" | "status"> & { backendId?: number };

type TriageHistoryEntry = NonNullable<TrackingLoopEvent["history"]>[number];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseIncidentStatus(value: unknown): Incident["status"] | null {
  if (typeof value !== "string") return null;
  const normalized = value.toLowerCase();
  if (normalized === "active") return "open";
  return normalized === "open" || normalized === "acknowledged" || normalized === "investigating" || normalized === "resolved"
    ? normalized
    : null;
}

function parseTriageHistory(value: unknown): TriageHistoryEntry[] | null {
  if (!Array.isArray(value)) return null;
  const entries: TriageHistoryEntry[] = [];
  for (const item of value) {
    if (!isRecord(item)) return null;
    if (
      typeof item.actor_user_id !== "number" ||
      !Number.isInteger(item.actor_user_id) ||
      typeof item.previous_status !== "string" ||
      typeof item.new_status !== "string" ||
      typeof item.created_at !== "string" ||
      !Number.isFinite(new Date(item.created_at).getTime()) ||
      (item.note !== undefined && item.note !== null && typeof item.note !== "string")
    ) return null;
    entries.push({
      actor_user_id: item.actor_user_id,
      previous_status: item.previous_status,
      new_status: item.new_status,
      note: item.note === null || item.note === undefined ? null : item.note,
      created_at: item.created_at,
    });
  }
  return entries;
}

function parseCanonicalIncidentDetail(value: unknown): { status: Incident["status"] | null; history: TriageHistoryEntry[] } | null {
  if (!isRecord(value)) return null;
  const history = parseTriageHistory(value.history);
  if (!history) return null;
  return { status: parseIncidentStatus(value.status), history };
}

interface AnomalyDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  incident: IncidentDetails | null;
  isLoading?: boolean;
}

type SeverityConfig = {
  label: string;
  color: string;
  bg: string;
  icon: typeof Flame;
};

const SEVERITY_CONFIG: Record<Incident["severity"], SeverityConfig> = {
  critical: { label: "CRITICAL", color: "#f85149", bg: "rgba(248,81,73,0.1)", icon: Flame },
  high:     { label: "HIGH",     color: "#ffa657", bg: "rgba(255,166,87,0.1)", icon: XCircle },
  medium:   { label: "MEDIUM",   color: "#d29922", bg: "rgba(210,153,34,0.1)", icon: Bell },
  low:      { label: "LOW",      color: "#7d8590", bg: "rgba(125,133,144,0.1)", icon: Clock },
};

export function AnomalyDrawer({ isOpen, onClose, incident, isLoading }: AnomalyDrawerProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const closeDialog = useCallback(onClose, [onClose]);
  useDialogFocus(dialogRef, isOpen, closeDialog);
  // Prevent body scrolling when open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "unset";
    }
    return () => {
      document.body.style.overflow = "unset";
    };
  }, [isOpen]);

  const [localStatus, setLocalStatus] = useState<Incident["status"]>("open");
  const [canonicalHistory, setCanonicalHistory] = useState<TriageHistoryEntry[] | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [auditLog, setAuditLog] = useState<{action: string; time: string; user: string}[]>([]);
  const [triageError, setTriageError] = useState<string | null>(null);

  useEffect(() => {
    if (incident) {
      setLocalStatus(incident.status || "open");
      setAuditLog(incident.history?.map((item) => ({
        action: `${item.previous_status} → ${item.new_status}${item.note ? `: ${item.note}` : ""}`,
        time: new Date(item.created_at).toLocaleString(),
        user: `User ${item.actor_user_id}`,
      })).reverse() ?? [{ action: "No persisted triage history returned", time: incident.timestamp || "Time unavailable", user: "System" }]);
    }
  }, [incident]);

  useEffect(() => {
    let cancelled = false;
    setCanonicalHistory(null);
    setDetailError(null);

    if (!isOpen || !incident?.backendId) {
      setDetailLoading(false);
      return () => { cancelled = true; };
    }

    setDetailLoading(true);
    void fetchAuthenticated(`/api/v1/tracking-loops/${incident.backendId}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const detail = parseCanonicalIncidentDetail(await response.json());
        if (!detail) throw new Error("Incident detail response schema invalid");
        if (cancelled) return;
        if (detail.status) setLocalStatus(detail.status);
        setCanonicalHistory(detail.history);
      })
      .catch(() => {
        if (!cancelled) setDetailError("Canonical incident history is unavailable; local event details are not presented as persisted.");
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });

    return () => { cancelled = true; };
  }, [incident, isOpen]);

  const displayedAuditLog = useMemo(() => {
    if (!canonicalHistory) return auditLog;
    if (canonicalHistory.length === 0) {
      return [{ action: "No persisted triage history returned", time: incident?.timestamp || "Time unavailable", user: "System" }];
    }
    return canonicalHistory.map((item) => ({
      action: `${item.previous_status} -> ${item.new_status}${item.note ? `: ${item.note}` : ""}`,
      time: new Date(item.created_at).toLocaleString(),
      user: `User ${item.actor_user_id}`,
    })).reverse();
  }, [auditLog, canonicalHistory, incident]);

  if (!isOpen) return null;

  const severity = incident?.severity ?? "medium";
  const sev = SEVERITY_CONFIG[severity];
  const SevIcon = sev.icon || AlertTriangle;

  const handleTriage = async (action: string, newStatus: Incident["status"]) => {
    if (!incident?.backendId) { setTriageError("This live-only event has no persisted incident record."); return; }
    setTriageError(null);
    try {
      const response = await fetchAuthenticated(`/api/v1/tracking-loops/${incident.backendId}/status`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: newStatus }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const detail = parseCanonicalIncidentDetail(await response.json());
      if (!detail) throw new Error("Incident mutation response schema invalid");
      setLocalStatus(detail.status ?? newStatus);
      setCanonicalHistory(detail.history);
      setAuditLog(prev => [{ action, time: new Date().toLocaleTimeString(), user: "Current User" }, ...prev]);
    } catch { setTriageError("The incident status was not saved or canonical history could not be read."); }
  };

  return (
    <>
      {/* Backdrop */}
      <div 
        aria-hidden="true"
        className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity"
        onClick={onClose}
      />
      
      {/* Drawer */}
      <div 
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="incident-dialog-title"
        tabIndex={-1}
        className="fixed right-0 top-0 bottom-0 w-full max-w-md bg-[#0d1117] border-l border-[#21262d] z-50 flex flex-col shadow-2xl transition-transform transform duration-300"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-[#21262d]">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-10 h-10 rounded-xl" style={{ background: sev.bg }}>
              <SevIcon className="w-5 h-5" style={{ color: sev.color }} />
            </div>
            <div>
              <h2 id="incident-dialog-title" className="text-[#e6edf3] text-lg font-bold leading-tight">
                {incident?.service || incident?.suspected_root_service || "Incident Details"}
              </h2>
              <span className="text-[#7d8590] text-xs">
                {incident?.timestamp || "Time unavailable"}
              </span>
            </div>
          </div>
          <button 
            type="button"
            aria-label="Close incident details"
            onClick={onClose}
            className="p-2 rounded-lg text-[#7d8590] hover:bg-[#21262d] hover:text-[#e6edf3] transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {isLoading ? (
            <div className="space-y-4">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-48 w-full" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : incident ? (
            <>
              {/* Summary Cards */}
              <div className="grid grid-cols-2 gap-3">
                <div className="p-4 rounded-xl bg-[#161b22] border border-[#21262d]">
                  <span className="text-[#7d8590] text-xs font-semibold uppercase tracking-wider">Severity</span>
                  <div className="mt-1 flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: sev.color }} />
                    <span className="text-[#e6edf3] font-bold">{sev.label}</span>
                  </div>
                </div>
                <div className="p-4 rounded-xl bg-[#161b22] border border-[#21262d]">
                  <span className="text-[#7d8590] text-xs font-semibold uppercase tracking-wider">Status</span>
                  <div className="mt-1 flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${localStatus === 'resolved' ? 'bg-[#3fb950]' : localStatus === 'investigating' ? 'bg-[#f59e0b] animate-pulse' : 'bg-[#f85149] animate-pulse'}`} />
                    <span className="text-[#e6edf3] font-bold capitalize">{localStatus}</span>
                  </div>
                </div>
              </div>

              {/* Anomaly Score */}
              {incident.anomaly_score !== undefined && (
                <div className="p-5 rounded-xl bg-[#161b22] border border-[#21262d]">
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-[#e6edf3] font-semibold flex items-center gap-2">
                      <Activity className="w-4 h-4 text-[#388bfd]" />
                      Isolation Forest Score
                    </span>
                    <span className="text-[#388bfd] font-mono text-sm bg-[#388bfd]/10 px-2 py-0.5 rounded">
                      {incident.anomaly_score.toFixed(3)}
                    </span>
                  </div>
                  
                  {/* Progress bar for score */}
                  <div className="w-full h-2 bg-[#21262d] rounded-full overflow-hidden">
                    <div 
                      className="h-full rounded-full transition-all duration-1000"
                      style={{ 
                        width: `${Math.min(100, incident.anomaly_score * 100)}%`,
                        background: `linear-gradient(90deg, ${sev.color}40, ${sev.color})` 
                      }}
                    />
                  </div>
                  <p className="text-[#7d8590] text-xs mt-3">
                    Normalized deviation score representing the severity of the pattern mismatch against the learned baseline.
                  </p>
                </div>
              )}

              {/* Description & Root Cause */}
              <div className="space-y-3">
                <h3 className="text-[#e6edf3] font-semibold flex items-center gap-2">
                  <ShieldAlert className="w-4 h-4 text-[#d29922]" />
                  AI Analysis & Root Cause
                </h3>
                <div className="p-4 rounded-xl bg-[#d29922]/5 border border-[#d29922]/20 text-[#c9d1d9] text-sm leading-relaxed">
                  {incident.description || "No incident description was returned."}
                  
                  {incident.root_cause_confidence !== undefined && incident.root_cause_confidence !== null && (
                    <div className="mt-3 flex items-center gap-2 text-xs text-[#d29922]">
                      <span className="font-semibold">Confidence:</span>
                      {Math.round(incident.root_cause_confidence * 100)}%
                    </div>
                  )}
                </div>
              </div>

              {/* Impact / Blast Radius */}
              {incident.blast_radius && incident.blast_radius.length > 0 && (
                <div className="space-y-3">
                  <h3 className="text-[#e6edf3] font-semibold text-sm">Affected Downstream Services</h3>
                  <div className="divide-y divide-[#21262d] border border-[#21262d] rounded-xl overflow-hidden bg-[#161b22]">
                    {incident.blast_radius.map((node: BlastRadiusNode, i: number) => (
                      <div key={i} className="flex items-center justify-between p-3 text-sm">
                        <span className="text-[#c9d1d9] font-medium">{node.service_name}</span>
                        <span className="text-[#7d8590] text-xs uppercase bg-[#0d1117] px-2 py-1 rounded">
                          {node.impact_classification || "indirect"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Audit Timeline */}
              <div className="space-y-3">
                <h3 className="text-[#e6edf3] font-semibold text-sm flex items-center gap-2">
                  <ListChecks className="w-4 h-4 text-[#388bfd]" />
                  Audit Timeline
                </h3>
                <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
                  <div className="relative border-l border-[#30363d] ml-3 space-y-4">
                    {detailLoading ? (
                      <div role="status" className="pl-5 text-[#7d8590] text-xs">Loading canonical triage history...</div>
                    ) : detailError ? (
                      <div role="alert" className="pl-5 text-[#d29922] text-xs">{detailError}</div>
                    ) : displayedAuditLog.map((log, i) => (
                      <div key={i} className="relative pl-5">
                        <div className="absolute -left-[5px] top-1.5 w-2 h-2 rounded-full bg-[#388bfd] ring-4 ring-[#161b22]" />
                        <div className="flex flex-col">
                          <span className="text-[#e6edf3] text-sm">{log.action}</span>
                          <span className="text-[#7d8590] text-xs">{log.user} • {log.time}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Actions */}
              <div className="pt-4 flex flex-col gap-3 pb-6">
                <div className="flex gap-3">
                  {localStatus === 'open' && (
                    <button 
                      onClick={() => void handleTriage("Acknowledged incident", "acknowledged")}
                      className="flex-1 py-2.5 rounded-lg bg-[#3fb950] text-white font-semibold text-sm hover:bg-[#2ea043] transition-colors"
                    >
                      Acknowledge
                    </button>
                  )}
                  {localStatus !== 'resolved' && (
                    <button 
                      onClick={() => void handleTriage("Marked incident investigating", "investigating")}
                      className="flex-1 py-2.5 flex items-center justify-center gap-2 rounded-lg bg-[#388bfd] text-white font-semibold text-sm hover:bg-[#2f81f7] transition-colors"
                    >
                      <UserPlus className="w-4 h-4" /> Assign
                    </button>
                  )}
                  {localStatus !== 'resolved' && (
                    <button 
                      onClick={() => void handleTriage("Resolved incident", "resolved")}
                      className="flex-1 py-2.5 flex items-center justify-center gap-2 rounded-lg border border-[#3fb950] text-[#3fb950] font-semibold text-sm hover:bg-[#3fb950]/10 transition-colors"
                    >
                      <CheckCircle className="w-4 h-4" /> Resolve
                    </button>
                  )}
                </div>
                {triageError && <p role="alert" className="text-sm text-red-400">{triageError}</p>}
                <button type="button" onClick={() => navigate(`/logs?service=${encodeURIComponent(incident.service)}&incident=${encodeURIComponent(incident.id)}`)} className="w-full py-2.5 rounded-lg bg-[#21262d] text-[#c9d1d9] font-semibold text-sm hover:bg-[#30363d] transition-colors">
                  View Raw Logs
                </button>
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-[#7d8590]">
              No incident data selected.
            </div>
          )}
        </div>
      </div>
    </>
  );
}

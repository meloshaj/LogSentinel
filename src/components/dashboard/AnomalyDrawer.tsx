import { X, Flame, Bell, Clock, Activity, ShieldAlert, XCircle, ArrowRight, AlertTriangle, UserPlus, CheckCircle, ListChecks } from "lucide-react";
import React, { useEffect, useState } from "react";
import type { TrackingLoopEvent } from "../../providers/TelemetryProvider";
import { Skeleton } from "../common/Skeleton";

interface AnomalyDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  incident: any | null; // using any for now, could be from activeTrackingLoops or Incident
  isLoading?: boolean;
}

const SEVERITY_CONFIG: Record<string, any> = {
  critical: { label: "CRITICAL", color: "#f85149", bg: "rgba(248,81,73,0.1)", icon: Flame },
  high:     { label: "HIGH",     color: "#ffa657", bg: "rgba(255,166,87,0.1)", icon: XCircle },
  medium:   { label: "MEDIUM",   color: "#d29922", bg: "rgba(210,153,34,0.1)", icon: Bell },
  low:      { label: "LOW",      color: "#7d8590", bg: "rgba(125,133,144,0.1)", icon: Clock },
};

export function AnomalyDrawer({ isOpen, onClose, incident, isLoading }: AnomalyDrawerProps) {
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

  if (!isOpen) return null;

  const [localStatus, setLocalStatus] = useState<string>("open");
  const [auditLog, setAuditLog] = useState<{action: string; time: string; user: string}[]>([]);

  useEffect(() => {
    if (incident) {
      setLocalStatus(incident.status || "open");
      setAuditLog([
        { action: "Incident auto-detected by AI", time: incident.timestamp || new Date().toLocaleTimeString(), user: "System" }
      ]);
    }
  }, [incident]);

  const severity = incident?.severity || "medium";
  const sev = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG.medium;
  const SevIcon = sev?.icon || AlertTriangle;

  const handleTriage = (action: string, newStatus: string) => {
    setLocalStatus(newStatus);
    setAuditLog(prev => [
      { action, time: new Date().toLocaleTimeString(), user: "Current User" },
      ...prev
    ]);
  };

  return (
    <>
      {/* Backdrop */}
      <div 
        className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity"
        onClick={onClose}
      />
      
      {/* Drawer */}
      <div 
        className="fixed right-0 top-0 bottom-0 w-full max-w-md bg-[#0d1117] border-l border-[#21262d] z-50 flex flex-col shadow-2xl transition-transform transform duration-300"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-[#21262d]">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-10 h-10 rounded-xl" style={{ background: sev.bg }}>
              <SevIcon className="w-5 h-5" style={{ color: sev.color }} />
            </div>
            <div>
              <h2 className="text-[#e6edf3] text-lg font-bold leading-tight">
                {incident?.service || incident?.suspected_root_service || "Incident Details"}
              </h2>
              <span className="text-[#7d8590] text-xs">
                {incident?.timestamp || new Date().toLocaleTimeString()}
              </span>
            </div>
          </div>
          <button 
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
                  {incident.description || `Anomaly loop detected in ${incident.service || incident.suspected_root_service}. The system identified unexpected log burst patterns correlating with performance degradation in downstream dependencies.`}
                  
                  {incident.root_cause_confidence && (
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
                    {incident.blast_radius.map((node: any, i: number) => (
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
                    {auditLog.map((log, i) => (
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
                      onClick={() => handleTriage("Acknowledged incident", "investigating")}
                      className="flex-1 py-2.5 rounded-lg bg-[#3fb950] text-white font-semibold text-sm hover:bg-[#2ea043] transition-colors"
                    >
                      Acknowledge
                    </button>
                  )}
                  {localStatus !== 'resolved' && (
                    <button 
                      onClick={() => handleTriage("Assigned incident to on-call", localStatus)}
                      className="flex-1 py-2.5 flex items-center justify-center gap-2 rounded-lg bg-[#388bfd] text-white font-semibold text-sm hover:bg-[#2f81f7] transition-colors"
                    >
                      <UserPlus className="w-4 h-4" /> Assign
                    </button>
                  )}
                  {localStatus !== 'resolved' && (
                    <button 
                      onClick={() => handleTriage("Resolved incident", "resolved")}
                      className="flex-1 py-2.5 flex items-center justify-center gap-2 rounded-lg border border-[#3fb950] text-[#3fb950] font-semibold text-sm hover:bg-[#3fb950]/10 transition-colors"
                    >
                      <CheckCircle className="w-4 h-4" /> Resolve
                    </button>
                  )}
                </div>
                <button className="w-full py-2.5 rounded-lg bg-[#21262d] text-[#c9d1d9] font-semibold text-sm hover:bg-[#30363d] transition-colors">
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

import { Activity, Radio, Signal, SignalHigh } from "lucide-react";
import { useMemo } from "react";
import { useTelemetrySocket } from "../../hooks/useTelemetrySocket";
import type { FeatureWindowClosedPayload, LogParsedPayload, TelemetryEvent } from "../../types/telemetry";

function isLogParsed(event: TelemetryEvent): event is TelemetryEvent & { payload: LogParsedPayload } {
  return event.type === "log.parsed";
}

function isFeatureWindow(event: TelemetryEvent): event is TelemetryEvent & { payload: FeatureWindowClosedPayload } {
  return event.type === "feature.window.closed";
}

function statusColor(status: string) {
  if (status === "connected") return "#3fb950";
  if (status === "connecting") return "#d29922";
  if (status === "error") return "#f85149";
  return "#7d8590";
}

export function LiveTelemetryStatus() {
  const { connectionStatus, eventCount, latestEvent, recentEvents } = useTelemetrySocket();
  const latestLog = useMemo(() => recentEvents.find(isLogParsed), [recentEvents]);
  const latestFeature = useMemo(() => recentEvents.find(isFeatureWindow), [recentEvents]);
  const color = statusColor(connectionStatus);

  return (
    <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-[#21262d]">
        <div className="flex items-center gap-2">
          <Radio className="w-4 h-4" style={{ color }} />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Backend Telemetry</span>
          <span className="px-2 py-0.5 rounded-full border" style={{ color, borderColor: `${color}55`, fontSize: "10px", fontWeight: 700 }}>
            {connectionStatus.toUpperCase()}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[#7d8590]" style={{ fontSize: "11px" }}>
          <span className="flex items-center gap-1"><SignalHigh className="w-3 h-3" /> {eventCount} events</span>
          <span>Latest: {latestEvent?.type ?? "none"}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 p-4">
        <div className="rounded-lg bg-[#0d1117] border border-[#21262d] p-3">
          <div className="flex items-center gap-2 mb-2 text-[#7d8590]" style={{ fontSize: "10px", fontWeight: 700, textTransform: "uppercase" }}>
            <Activity className="w-3 h-3" /> Latest parsed log
          </div>
          {latestLog ? (
            <div className="space-y-1">
              <div className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 600 }}>{latestLog.payload.service}</div>
              <div className="text-[#7d8590]" style={{ fontSize: "11px" }}>{latestLog.payload.level} - template {latestLog.payload.template_id}</div>
              <div className="text-[#484f58] truncate" style={{ fontSize: "10px" }}>{latestLog.payload.template ?? "No template text"}</div>
            </div>
          ) : (
            <span className="text-[#484f58]" style={{ fontSize: "11px" }}>Waiting for parsed logs</span>
          )}
        </div>

        <div className="rounded-lg bg-[#0d1117] border border-[#21262d] p-3">
          <div className="flex items-center gap-2 mb-2 text-[#7d8590]" style={{ fontSize: "10px", fontWeight: 700, textTransform: "uppercase" }}>
            <Signal className="w-3 h-3" /> Latest feature window
          </div>
          {latestFeature ? (
            <div className="space-y-1">
              <div className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 600 }}>{latestFeature.payload.window_id}</div>
              <div className="text-[#7d8590]" style={{ fontSize: "11px" }}>
                {latestFeature.payload.total_log_count} logs - {latestFeature.payload.error_count} errors
              </div>
              <div className="text-[#484f58]" style={{ fontSize: "10px" }}>
                {latestFeature.payload.unique_templates} templates
              </div>
            </div>
          ) : (
            <span className="text-[#484f58]" style={{ fontSize: "11px" }}>Waiting for closed windows</span>
          )}
        </div>

        <div className="rounded-lg bg-[#0d1117] border border-[#21262d] p-3">
          <div className="mb-2 text-[#7d8590]" style={{ fontSize: "10px", fontWeight: 700, textTransform: "uppercase" }}>Recent events</div>
          <div className="space-y-1">
            {recentEvents.slice(0, 5).map((event, index) => (
              <div key={`${event.timestamp}-${event.type}-${index}`} className="flex items-center justify-between gap-2">
                <span className="text-[#c9d1d9] truncate" style={{ fontSize: "11px" }}>{event.type}</span>
                <span className="text-[#484f58] shrink-0" style={{ fontSize: "10px" }}>{new Date(event.timestamp).toLocaleTimeString()}</span>
              </div>
            ))}
            {recentEvents.length === 0 && <span className="text-[#484f58]" style={{ fontSize: "11px" }}>No events yet</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

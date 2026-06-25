import { MetricCards } from "../components/dashboard/MetricCards";
import { TrafficChart } from "../components/dashboard/TrafficChart";
import { mockIncidents, mockLogs } from "../services/mockMonitoringData";
import { Activity, AlertTriangle, ArrowRight, CheckCircle, Clock } from "lucide-react";
import { useNavigate } from "react-router";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "#f85149",
  high: "#ffa657",
  medium: "#d29922",
  low: "#7d8590",
};

function QuickStat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col gap-1 p-3 rounded-lg bg-[#0d1117] border border-[#21262d]">
      <span className="text-[#484f58]" style={{ fontSize: "10px" }}>{label}</span>
      <span style={{ fontSize: "20px", fontWeight: 700, color }}>{value}</span>
    </div>
  );
}

export function OverviewPage() {
  const navigate = useNavigate();
  const recentLogs = mockLogs.slice(-6).reverse();
  const openIncidents = mockIncidents.filter((i) => i.status !== "resolved");

  return (
    <div className="space-y-5">
      {/* Metric cards */}
      <MetricCards />

      {/* Quick stats row */}
      <div className="grid grid-cols-4 gap-3">
        <QuickStat label="Logs today" value="14.2M" color="#e6edf3" />
        <QuickStat label="P99 Latency" value="843ms" color="#f85149" />
        <QuickStat label="Error rate" value="11.7%" color="#d29922" />
        <QuickStat label="Uptime (30d)" value="99.1%" color="#3fb950" />
      </div>

      {/* Traffic chart */}
      <TrafficChart />

      {/* Bottom row: recent logs + open incidents */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Recent log activity */}
        <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d]">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-[#388bfd]" />
              <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Recent Activity</span>
            </div>
            <button
              onClick={() => navigate("/logs")}
              className="flex items-center gap-1 text-[#388bfd] hover:text-[#79c0ff] transition-colors"
              style={{ fontSize: "11px" }}
            >
              View all logs <ArrowRight className="w-3 h-3" />
            </button>
          </div>
          <div className="divide-y divide-[#21262d]">
            {recentLogs.map((log) => {
              const colors: Record<string, string> = { INFO: "#79c0ff", WARN: "#d29922", ERROR: "#f85149", DEBUG: "#7d8590" };
              return (
                <div key={log.id} className="flex items-start gap-3 px-4 py-2.5">
                  <span
                    className="shrink-0 mt-0.5"
                    style={{ fontSize: "9px", fontWeight: 700, fontFamily: "monospace", color: colors[log.level], minWidth: 36 }}
                  >
                    {log.level}
                  </span>
                  <span className="text-[#484f58] shrink-0 mt-0.5" style={{ fontSize: "10px", fontFamily: "monospace" }}>
                    {log.timestamp}
                  </span>
                  <span className="text-[#7d8590] truncate" style={{ fontSize: "11px", fontFamily: "monospace" }}>
                    {log.message}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Open incidents */}
        <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d]">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-[#ffa657]" />
              <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Open Incidents</span>
              <span className="px-1.5 py-0.5 rounded-full bg-[#da3633] text-white" style={{ fontSize: "10px", fontWeight: 700 }}>
                {openIncidents.length}
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
          <div className="divide-y divide-[#21262d]">
            {openIncidents.map((incident) => (
              <div key={incident.id} className="flex items-start gap-3 px-4 py-3">
                <span
                  className="w-2 h-2 rounded-full shrink-0 mt-1.5"
                  style={{ background: SEVERITY_COLOR[incident.severity] }}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 600 }}>{incident.service}</span>
                    <span className="text-[#484f58]" style={{ fontSize: "10px" }}>
                      <Clock className="w-2.5 h-2.5 inline mr-0.5" />{incident.timestamp}
                    </span>
                  </div>
                  <p className="text-[#7d8590] mt-0.5 truncate" style={{ fontSize: "10px" }}>{incident.description}</p>
                </div>
              </div>
            ))}
            {openIncidents.length === 0 && (
              <div className="flex items-center justify-center gap-2 py-8 text-[#3fb950]">
                <CheckCircle className="w-4 h-4" />
                <span style={{ fontSize: "12px" }}>All systems nominal</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

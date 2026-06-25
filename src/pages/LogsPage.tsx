import { LogStream } from "../components/logs/LogStream";
import { mockLogs } from "../services/mockMonitoringData";
import { Download, Search, SlidersHorizontal } from "lucide-react";
import { useState } from "react";

const services = ["All Services", "api-gateway", "auth-service", "payment-service", "database-service", "cache-service", "notification-svc"];
const timeRanges = ["Last 15 min", "Last 1 hour", "Last 6 hours", "Last 24 hours"];

function LogHistoryTable() {
  const rows = mockLogs.slice(0, 10);
  const levelColor: Record<string, string> = { INFO: "#79c0ff", WARN: "#d29922", ERROR: "#f85149", DEBUG: "#7d8590" };
  const levelBg: Record<string, string> = { INFO: "rgba(31,111,235,0.15)", WARN: "rgba(210,153,34,0.15)", ERROR: "rgba(218,54,51,0.15)", DEBUG: "rgba(125,133,144,0.1)" };

  return (
    <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d]">
        <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Log History</span>
        <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors" style={{ fontSize: "11px" }}>
          <Download className="w-3.5 h-3.5" /> Export CSV
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[#21262d]">
              {["Timestamp", "Level", "Service", "Message"].map((h) => (
                <th key={h} className="px-4 py-2.5 text-left text-[#484f58]" style={{ fontSize: "10px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[#21262d]/50">
            {rows.map((log) => (
              <tr key={log.id} className="hover:bg-[#21262d]/30 transition-colors">
                <td className="px-4 py-2.5 text-[#484f58]" style={{ fontSize: "11px", fontFamily: "monospace", whiteSpace: "nowrap" }}>{log.timestamp}</td>
                <td className="px-4 py-2.5">
                  <span
                    className="px-1.5 py-0.5 rounded"
                    style={{ fontSize: "9px", fontWeight: 700, fontFamily: "monospace", color: levelColor[log.level], background: levelBg[log.level] }}
                  >
                    {log.level}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-[#7d8590]" style={{ fontSize: "11px", fontFamily: "monospace", whiteSpace: "nowrap" }}>{log.service}</td>
                <td className="px-4 py-2.5 text-[#c9d1d9]" style={{ fontSize: "11px", fontFamily: "monospace" }}>{log.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function LogsPage() {
  const [service, setService] = useState("All Services");
  const [timeRange, setTimeRange] = useState("Last 15 min");
  const [search, setSearch] = useState("");

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Live Logs</h1>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Real-time log ingestion from all services - 2,450 logs/min</p>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Search */}
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[#161b22] border border-[#21262d] flex-1 min-w-48">
          <Search className="w-3.5 h-3.5 text-[#484f58] shrink-0" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search log messages, services, trace IDs..."
            className="bg-transparent text-[#e6edf3] placeholder:text-[#484f58] outline-none w-full"
            style={{ fontSize: "12px" }}
          />
        </div>

        {/* Service filter */}
        <select
          value={service}
          onChange={(e) => setService(e.target.value)}
          className="px-3 py-2 rounded-lg bg-[#161b22] border border-[#21262d] text-[#7d8590] outline-none cursor-pointer"
          style={{ fontSize: "12px" }}
        >
          {services.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>

        {/* Time range */}
        <select
          value={timeRange}
          onChange={(e) => setTimeRange(e.target.value)}
          className="px-3 py-2 rounded-lg bg-[#161b22] border border-[#21262d] text-[#7d8590] outline-none cursor-pointer"
          style={{ fontSize: "12px" }}
        >
          {timeRanges.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>

        <button className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[#161b22] border border-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors" style={{ fontSize: "12px" }}>
          <SlidersHorizontal className="w-3.5 h-3.5" /> Filters
        </button>
      </div>

      {/* Live stream */}
      <div style={{ height: 400 }}>
        <LogStream />
      </div>

      {/* Log history table */}
      <LogHistoryTable />
    </div>
  );
}

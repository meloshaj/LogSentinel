import { mockTimeSeriesData } from "../../services/mockMonitoringData";
import { BarChart2 } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function TrafficChart() {
  return (
    <div className="flex flex-col rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d]">
        <div className="flex items-center gap-2">
          <BarChart2 className="w-4 h-4 text-[#388bfd]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Log Volume & Error Rate</span>
        </div>
        <div className="flex gap-3">
          {[
            { color: "#388bfd", label: "Log volume" },
            { color: "#f85149", label: "Errors" },
            { color: "#d29922", label: "Anomalies" },
          ].map((item) => (
            <div key={item.label} className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full" style={{ background: item.color }} />
              <span className="text-[#7d8590]" style={{ fontSize: "10px" }}>{item.label}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="px-4 pb-4 pt-2">
        <ResponsiveContainer width="100%" height={140}>
          <AreaChart data={mockTimeSeriesData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="gradLogs" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#388bfd" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#388bfd" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gradErrors" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#da3633" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#da3633" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gradAnomalies" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#d29922" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#d29922" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid key="grid" strokeDasharray="3 3" stroke="#21262d" vertical={false} />
            <XAxis key="xaxis" dataKey="time" tick={{ fill: "#484f58", fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis key="yaxis" tick={{ fill: "#484f58", fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip
              key="tooltip"
              contentStyle={{ background: "#0d1117", border: "1px solid #21262d", borderRadius: 6, fontSize: 10 }}
              labelStyle={{ color: "#7d8590" }}
              itemStyle={{ color: "#e6edf3" }}
            />
            <Area key="logs"      type="monotone" dataKey="logs"      stroke="#388bfd" strokeWidth={1.5} fill="url(#gradLogs)"      dot={false} />
            <Area key="errors"    type="monotone" dataKey="errors"    stroke="#f85149" strokeWidth={1.5} fill="url(#gradErrors)"    dot={false} />
            <Area key="anomalies" type="monotone" dataKey="anomalies" stroke="#d29922" strokeWidth={1.5} fill="url(#gradAnomalies)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

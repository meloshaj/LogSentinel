import { mockTimeSeriesData } from "../services/mockMonitoringData";
import { BarChart2, TrendingDown, TrendingUp } from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

const servicePerf = [
  { service: "cache-svc",    uptime: 99.9, p95: 4,    errorRate: 0.1 },
  { service: "auth-svc",     uptime: 99.7, p95: 210,  errorRate: 0.8 },
  { service: "api-gw",       uptime: 99.1, p95: 430,  errorRate: 3.2 },
  { service: "notif-svc",    uptime: 98.8, p95: 95,   errorRate: 0.4 },
  { service: "payment-svc",  uptime: 94.2, p95: 1800, errorRate: 8.7 },
  { service: "db-svc",       uptime: 89.3, p95: 2400, errorRate: 14.1 },
];

const errorDistrib = [
  { name: "DB Timeout",        count: 142, fill: "#f85149" },
  { name: "Circuit Open",      count: 89,  fill: "#ffa657" },
  { name: "Rate Limited",      count: 41,  fill: "#d29922" },
  { name: "Auth Failure",      count: 18,  fill: "#bc8cff" },
  { name: "Upstream 503",      count: 67,  fill: "#388bfd" },
];

const healthData = [
  { name: "Health", value: 82, fill: "#3fb950" },
];

export function AnalyticsPage() {
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Analytics</h1>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Error trends, service performance, and usage statistics</p>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "Logs Today",       value: "14.2M",  delta: "+12%", up: true },
          { label: "Avg Error Rate",   value: "4.6%",   delta: "+2.1%", up: false },
          { label: "MTTR",             value: "18 min",  delta: "-3 min", up: true },
          { label: "SLO Compliance",   value: "97.4%",  delta: "-1.2%", up: false },
        ].map((k) => (
          <div key={k.label} className="p-4 rounded-xl bg-[#161b22] border border-[#21262d]">
            <div className="text-[#484f58]" style={{ fontSize: "10px" }}>{k.label}</div>
            <div className="text-[#e6edf3] mt-1" style={{ fontSize: "22px", fontWeight: 700 }}>{k.value}</div>
            <div className={`flex items-center gap-1 mt-0.5 ${k.up ? "text-[#3fb950]" : "text-[#f85149]"}`}>
              {k.up ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
              <span style={{ fontSize: "10px" }}>{k.delta} vs yesterday</span>
            </div>
          </div>
        ))}
      </div>

      {/* Volume + error trend chart */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <BarChart2 className="w-4 h-4 text-[#388bfd]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Log Volume & Error Trend</span>
        </div>
        <div className="px-4 pb-4 pt-2">
          <ResponsiveContainer width="100%" height={160}>
            <AreaChart data={mockTimeSeriesData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="ana-grad-logs" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#388bfd" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#388bfd" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="ana-grad-errors" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#da3633" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#da3633" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid key="grid" strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis key="x" dataKey="time" tick={{ fill: "#484f58", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis key="y" tick={{ fill: "#484f58", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip key="tip" contentStyle={{ background: "#0d1117", border: "1px solid #21262d", borderRadius: 6, fontSize: 10 }} labelStyle={{ color: "#7d8590" }} itemStyle={{ color: "#e6edf3" }} />
              <Area key="logs"   type="monotone" dataKey="logs"   stroke="#388bfd" strokeWidth={1.5} fill="url(#ana-grad-logs)"   dot={false} />
              <Area key="errors" type="monotone" dataKey="errors" stroke="#f85149" strokeWidth={1.5} fill="url(#ana-grad-errors)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Bottom row: error distribution + service perf + health score */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Error distribution bar */}
        <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
          <div className="px-4 py-3 border-b border-[#21262d]">
            <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Error Distribution</span>
          </div>
          <div className="px-4 pb-4 pt-2">
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={errorDistrib} layout="vertical" margin={{ top: 0, right: 20, left: 10, bottom: 0 }} barSize={10}>
                <XAxis key="x" type="number" tick={{ fill: "#484f58", fontSize: 9 }} axisLine={false} tickLine={false} />
                <YAxis key="y" type="category" dataKey="name" tick={{ fill: "#7d8590", fontSize: 10 }} axisLine={false} tickLine={false} width={80} />
                <Tooltip key="tip" contentStyle={{ background: "#0d1117", border: "1px solid #21262d", borderRadius: 6, fontSize: 10 }} labelStyle={{ color: "#7d8590" }} itemStyle={{ color: "#e6edf3" }} />
                <Bar key="count" dataKey="count" radius={[0, 3, 3, 0]}>
                  {errorDistrib.map((e, i) => <Cell key={i} fill={e.fill} fillOpacity={0.8} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Service performance table */}
        <div className="lg:col-span-2 rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
          <div className="px-4 py-3 border-b border-[#21262d]">
            <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Service Performance</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#21262d]">
                  {["Service", "Uptime", "P95 Latency", "Error Rate", "Status"].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-left text-[#484f58]" style={{ fontSize: "10px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[#21262d]/50">
                {servicePerf.map((s) => {
                  const healthy = s.uptime > 98;
                  const warn = s.uptime >= 94 && s.uptime <= 98;
                  const statusColor = healthy ? "#3fb950" : warn ? "#d29922" : "#f85149";
                  const statusLabel = healthy ? "Healthy" : warn ? "Degraded" : "Critical";
                  return (
                    <tr key={s.service} className="hover:bg-[#21262d]/30 transition-colors">
                      <td className="px-4 py-2.5 text-[#e6edf3]" style={{ fontSize: "12px", fontFamily: "monospace" }}>{s.service}</td>
                      <td className="px-4 py-2.5" style={{ fontSize: "12px", color: statusColor }}>{s.uptime}%</td>
                      <td className="px-4 py-2.5 text-[#7d8590]" style={{ fontSize: "12px" }}>{s.p95}ms</td>
                      <td className="px-4 py-2.5" style={{ fontSize: "12px", color: s.errorRate > 5 ? "#f85149" : s.errorRate > 2 ? "#d29922" : "#7d8590" }}>{s.errorRate}%</td>
                      <td className="px-4 py-2.5">
                        <span className="px-2 py-0.5 rounded-full" style={{ fontSize: "10px", fontWeight: 600, color: statusColor, background: `${statusColor}20` }}>{statusLabel}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

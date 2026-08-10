import { useLiveLogs } from "../hooks/useLiveLogs";
import { useMemo } from "react";
import { BarChart2, TrendingDown, TrendingUp } from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

export function AnalyticsPage() {
  const { filteredLogs, totalLogCount } = useLiveLogs();

  const timeSeriesData = useMemo(() => {
    if (filteredLogs.length === 0) return [];
    const buckets: Record<string, { time: string, logs: number, errors: number }> = {};
    filteredLogs.forEach(log => {
      const minute = log.timestamp.split(':').slice(0, 2).join(':');
      if (!buckets[minute]) buckets[minute] = { time: minute, logs: 0, errors: 0 };
      buckets[minute].logs += 1;
      if (log.level === 'ERROR' || log.level === 'FATAL') buckets[minute].errors += 1;
    });
    return Object.values(buckets).slice(-20);
  }, [filteredLogs]);

  const servicePerf = useMemo(() => {
    const stats: Record<string, { total: number; errors: number; latencies: number[] }> = {};
    filteredLogs.forEach(log => {
      const svc = log.service || "unknown";
      if (!stats[svc]) stats[svc] = { total: 0, errors: 0, latencies: [] };
      stats[svc].total += 1;
      if (log.level === 'ERROR' || log.level === 'FATAL') {
        stats[svc].errors += 1;
      }
      if (log.latency_ms !== undefined) {
        stats[svc].latencies.push(log.latency_ms);
      }
    });
    
    return Object.entries(stats).map(([service, data]) => {
      const errorRate = data.total > 0 ? (data.errors / data.total) * 100 : 0;
      const uptime = Math.max(0, 100 - errorRate);
      
      let p95 = 0;
      if (data.latencies.length > 0) {
        data.latencies.sort((a, b) => a - b);
        p95 = data.latencies[Math.floor(data.latencies.length * 0.95)];
      }

      return {
        service,
        uptime: Number(uptime.toFixed(1)),
        p95: p95.toFixed(0),
        errorRate: Number(errorRate.toFixed(1))
      };
    }).sort((a, b) => b.errorRate - a.errorRate);
  }, [filteredLogs]);

  const errorDistrib = useMemo(() => {
    const counts: Record<string, number> = {};
    filteredLogs.forEach(log => {
      if (log.level === 'ERROR') {
        // extract a short reason or just use the service name
        let reason = "Unknown Error";
        if (log.message.includes("timeout") || log.message.includes("Timeout")) reason = "Timeout";
        else if (log.message.includes("pool")) reason = "Pool Exhausted";
        else if (log.message.includes("auth") || log.message.includes("JWT")) reason = "Auth Failure";
        else if (log.message.includes("network")) reason = "Network Partition";
        else if (log.message.includes("deadlock")) reason = "Deadlock";
        else reason = log.service;
        
        counts[reason] = (counts[reason] || 0) + 1;
      }
    });
    
    const colors = ["#f85149", "#ffa657", "#d29922", "#bc8cff", "#388bfd"];
    return Object.entries(counts)
      .map(([name, count], idx) => ({ name, count, fill: colors[idx % colors.length] }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 5);
  }, [filteredLogs]);

  const totalErrors = filteredLogs.filter(l => l.level === 'ERROR').length;
  const avgErrorRate = filteredLogs.length > 0 ? ((totalErrors / filteredLogs.length) * 100).toFixed(1) : "0.0";
  const sloCompliance = filteredLogs.length > 0 ? (100 - Number(avgErrorRate)).toFixed(1) : "100.0";

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Analytics</h1>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Error trends, service performance, and usage statistics</p>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "Logs Ingested",    value: totalLogCount.toLocaleString(),  delta: "Live", up: true },
          { label: "Avg Error Rate",   value: `${avgErrorRate}%`,   delta: "Live", up: Number(avgErrorRate) < 5 },
          { label: "Active Services",  value: servicePerf.length.toString(),  delta: "Live", up: true },
          { label: "SLO Compliance",   value: `${sloCompliance}%`,  delta: "Live", up: Number(sloCompliance) > 95 },
        ].map((k) => (
          <div key={k.label} className="p-4 rounded-xl bg-[#161b22] border border-[#21262d]">
            <div className="text-[#484f58]" style={{ fontSize: "10px" }}>{k.label}</div>
            <div className="text-[#e6edf3] mt-1" style={{ fontSize: "22px", fontWeight: 700 }}>{k.value}</div>
            <div className={`flex items-center gap-1 mt-0.5 ${k.up ? "text-[#3fb950]" : "text-[#f85149]"}`}>
              {k.up ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
              <span style={{ fontSize: "10px" }}>{k.delta}</span>
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
            <AreaChart data={timeSeriesData.length > 0 ? timeSeriesData : [{ time: '00:00', logs: 0, errors: 0 }]} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
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

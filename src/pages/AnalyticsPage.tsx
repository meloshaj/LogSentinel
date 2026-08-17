import { useLiveLogs } from "../hooks/useLiveLogs";
import { useMemo } from "react";
import { BarChart2, TrendingDown, TrendingUp, AlertTriangle, ShieldCheck, CheckCircle } from "lucide-react";
import {
  Area, ComposedChart, Bar, BarChart, CartesianGrid, Cell,
  ResponsiveContainer, Tooltip, XAxis, YAxis, Line,
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
      if (log.level === 'ERROR' || log.level === 'FATAL') {
        buckets[minute].errors += 1;
      }
    });

    return Object.values(buckets)
      .sort((a, b) => a.time.localeCompare(b.time))
      .slice(-20);
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
        p95 = data.latencies[Math.floor(data.latencies.length * 0.95)] ?? 0;
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
      if (log.level === 'ERROR' || log.level === 'FATAL') {
        let reason = "Unknown Error";
        const msg = (log.message || "").toLowerCase();
        if (msg.includes("timeout")) reason = "Timeout";
        else if (msg.includes("pool") || msg.includes("connection")) reason = "Connection Pool";
        else if (msg.includes("auth") || msg.includes("jwt") || msg.includes("token")) reason = "Auth Failure";
        else if (msg.includes("network") || msg.includes("econnrefused")) reason = "Network / Partition";
        else if (msg.includes("deadlock") || msg.includes("lock")) reason = "DB Deadlock";
        else reason = log.service || "General Error";
        
        counts[reason] = (counts[reason] || 0) + 1;
      }
    });
    
    const colors = ["#f85149", "#ffa657", "#d29922", "#bc8cff", "#388bfd"];
    return Object.entries(counts)
      .map(([name, count], idx) => ({ name, count, fill: colors[idx % colors.length] }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 5);
  }, [filteredLogs]);

  const totalErrors = filteredLogs.filter(l => l.level === 'ERROR' || l.level === 'FATAL').length;
  const avgErrorRate = filteredLogs.length > 0 ? ((totalErrors / filteredLogs.length) * 100).toFixed(1) : "0.0";
  const sloCompliance = filteredLogs.length > 0 ? (100 - Number(avgErrorRate)).toFixed(1) : "100.0";

  const CustomChartTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div className="bg-[#0d1117]/95 backdrop-blur-md border border-[#21262d] rounded-xl p-3.5 shadow-2xl min-w-[190px]">
          <p className="text-[#e6edf3] font-bold text-xs mb-2.5 border-b border-[#21262d] pb-1.5">{label} UTC</p>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between items-center gap-4">
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-sm bg-[#388bfd]" />
                <span className="text-[#8b949e]">Total Logs</span>
              </div>
              <span className="text-[#e6edf3] font-mono font-bold">{data.logs}</span>
            </div>
            <div className="flex justify-between items-center gap-4">
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-[#f85149]" />
                <span className="text-[#8b949e]">Errors</span>
              </div>
              <span className="text-[#f85149] font-mono font-bold">{data.errors}</span>
            </div>
          </div>
        </div>
      );
    }
    return null;
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Telemetry Analytics</h1>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Error trends, service performance distributions, and SLA metrics</p>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "Logs Ingested",    value: totalLogCount.toLocaleString(),  delta: "Live Stream", up: true },
          { label: "Avg Error Rate",   value: `${avgErrorRate}%`,   delta: `${totalErrors} errors`, up: Number(avgErrorRate) < 5 },
          { label: "Active Services",  value: servicePerf.length.toString(),  delta: "Monitored", up: true },
          { label: "SLO Compliance",   value: `${sloCompliance}%`,  delta: "Availability", up: Number(sloCompliance) > 95 },
        ].map((k) => (
          <div key={k.label} className="p-4 rounded-xl bg-[#161b22] border border-[#21262d] shadow-sm">
            <div className="text-[#8b949e]" style={{ fontSize: "11px", fontWeight: 500 }}>{k.label}</div>
            <div className="text-[#e6edf3] mt-1 font-mono" style={{ fontSize: "22px", fontWeight: 700 }}>{k.value}</div>
            <div className={`flex items-center gap-1 mt-1 text-xs font-semibold ${k.up ? "text-[#3fb950]" : "text-[#f85149]"}`}>
              {k.up ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
              <span>{k.delta}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Volume + error trend chart with Dual Y-Axis */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-[#21262d]">
          <div className="flex items-center gap-2">
            <BarChart2 className="w-4 h-4 text-[#388bfd]" />
            <span className="text-[#e6edf3] text-[13px] font-bold">Log Volume & Error Trend</span>
          </div>

          <div className="flex items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm bg-[#388bfd]" />
              <span className="text-[#8b949e]">Log Volume</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-[#f85149]" />
              <span className="text-[#f85149] font-semibold">Error Count</span>
            </div>
          </div>
        </div>

        <div className="px-4 pb-4 pt-2">
          <ResponsiveContainer width="100%" height={170}>
            <ComposedChart 
              data={timeSeriesData.length > 0 ? timeSeriesData : [{ time: '00:00', logs: 0, errors: 0 }]} 
              margin={{ top: 15, right: 10, left: -20, bottom: 0 }}
            >
              <defs>
                <linearGradient id="ana-grad-logs" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#388bfd" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#388bfd" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="ana-grad-errors" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f85149" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#f85149" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={{ stroke: "#30363d" }} tickLine={false} dy={5} />
              <YAxis yAxisId="left" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={false} tickLine={false} dx={-5} />
              <YAxis yAxisId="right" orientation="right" tick={{ fill: "#f85149", fontSize: 10 }} axisLine={false} tickLine={false} dx={5} />
              
              <Tooltip content={<CustomChartTooltip />} cursor={{ fill: "#21262d", opacity: 0.4 }} />
              
              {/* Logs Area (Left Axis) */}
              <Area 
                yAxisId="left"
                type="monotone" 
                dataKey="logs" 
                name="Logs"
                stroke="#388bfd" 
                strokeWidth={1.5} 
                fill="url(#ana-grad-logs)" 
                dot={false} 
              />

              {/* Errors Line (Right Axis so error spikes are clearly visible) */}
              <Line 
                yAxisId="right"
                type="monotone" 
                dataKey="errors" 
                name="Errors"
                stroke="#f85149" 
                strokeWidth={2} 
                dot={{ r: 2.5, fill: "#f85149", strokeWidth: 0 }} 
                activeDot={{ r: 4.5, fill: "#f85149" }} 
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Bottom row: error distribution + service perf */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Error distribution bar */}
        <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm flex flex-col">
          <div className="px-4 py-3 border-b border-[#21262d] flex items-center justify-between">
            <span className="text-[#e6edf3] text-[13px] font-bold">Error Classification</span>
            <span className="text-[#7d8590] text-[10px]">{totalErrors} Total</span>
          </div>
          <div className="px-4 pb-4 pt-3 flex-1 flex flex-col justify-center">
            {errorDistrib.length > 0 ? (
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={errorDistrib} layout="vertical" margin={{ top: 0, right: 20, left: 10, bottom: 0 }} barSize={10}>
                  <XAxis type="number" tick={{ fill: "#8b949e", fontSize: 9 }} axisLine={false} tickLine={false} />
                  <YAxis type="category" dataKey="name" tick={{ fill: "#c9d1d9", fontSize: 10 }} axisLine={false} tickLine={false} width={90} />
                  <Tooltip 
                    contentStyle={{ background: "#0d1117", border: "1px solid #21262d", borderRadius: 8, fontSize: 11 }} 
                    itemStyle={{ color: "#e6edf3" }} 
                  />
                  <Bar dataKey="count" radius={[0, 3, 3, 0]}>
                    {errorDistrib.map((e, i) => <Cell key={i} fill={e.fill} fillOpacity={0.85} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex flex-col items-center justify-center py-8 text-center text-[#7d8590]">
                <CheckCircle className="w-6 h-6 text-[#3fb950] mb-1.5" />
                <span className="text-xs font-semibold text-[#e6edf3]">Zero Errors Detected</span>
                <span className="text-[10px] text-[#7d8590] mt-0.5">All service telemetry is clean.</span>
              </div>
            )}
          </div>
        </div>

        {/* Service performance table */}
        <div className="lg:col-span-2 rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm flex flex-col">
          <div className="px-4 py-3 border-b border-[#21262d] flex items-center justify-between">
            <span className="text-[#e6edf3] text-[13px] font-bold">Service Performance Metrics</span>
            <span className="text-[#7d8590] text-[10px]">{servicePerf.length} Services</span>
          </div>
          <div className="overflow-x-auto flex-1">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#21262d] bg-[#0d1117]/50">
                  {["Service", "Uptime", "P95 Latency", "Error Rate", "Status"].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-left text-[#8b949e] text-[10px] font-bold uppercase tracking-wider">{h}</th>
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
                      <td className="px-4 py-2.5 text-[#e6edf3] text-xs font-mono font-semibold">{s.service}</td>
                      <td className="px-4 py-2.5 text-xs font-mono font-bold" style={{ color: statusColor }}>{s.uptime}%</td>
                      <td className="px-4 py-2.5 text-[#c9d1d9] text-xs font-mono">{s.p95}ms</td>
                      <td className="px-4 py-2.5 text-xs font-mono font-semibold" style={{ color: s.errorRate > 5 ? "#f85149" : s.errorRate > 2 ? "#d29922" : "#7d8590" }}>
                        {s.errorRate}%
                      </td>
                      <td className="px-4 py-2.5">
                        <span className="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider" style={{ color: statusColor, background: `${statusColor}20`, border: `1px solid ${statusColor}40` }}>
                          {statusLabel}
                        </span>
                      </td>
                    </tr>
                  );
                })}
                {servicePerf.length === 0 && (
                  <tr>
                    <td colSpan={5} className="text-center py-8 text-[#7d8590] text-xs">
                      No service telemetry recorded yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

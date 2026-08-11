import { BarChart2, Filter } from "lucide-react";
import {
  Bar,
  Line,
  ComposedChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Scatter,
} from "recharts";
import { useLiveLogs } from "../../hooks/useLiveLogs";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";
import { useMemo, useState } from "react";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "#f85149",
  high: "#f85149",
  medium: "#d29922",
  low: "#388bfd",
};

export function TrafficChart() {
  const { filteredLogs } = useLiveLogs();
  const { activeTrackingLoops } = useTelemetryStream();
  
  const [filters, setFilters] = useState({
    high: true,
    medium: true,
    low: true,
  });

  const toggleFilter = (level: keyof typeof filters) => {
    setFilters(prev => ({ ...prev, [level]: !prev[level] }));
  };

  const timeSeriesData = useMemo(() => {
    if (filteredLogs.length === 0) return [];
    
    // Bucket logs by minute
    const buckets: Record<string, any> = {};
    
    filteredLogs.forEach(log => {
      const minute = log.timestamp.split(':').slice(0, 2).join(':');
      if (!buckets[minute]) {
        buckets[minute] = { time: minute, logs: 0, errors: 0, anomalies: 0 };
      }
      buckets[minute].logs += 1;
      if (log.level === 'ERROR') {
        buckets[minute].errors += 1;
      }
    });

    // Process tracking loops to overlay anomalies on the correct minute buckets
    activeTrackingLoops.forEach(loop => {
      const isHigh = loop.severity === 'critical' || loop.severity === 'high';
      const isMed = loop.severity === 'medium';
      const isLow = loop.severity === 'low';
      
      if (
        (isHigh && !filters.high) ||
        (isMed && !filters.medium) ||
        (isLow && !filters.low)
      ) {
        return;
      }

      // fallback to current time if no created_at
      const d = (loop as any).created_at ? new Date((loop as any).created_at) : new Date();
      const minute = d.toTimeString().split(':').slice(0, 2).join(':');
      
      if (buckets[minute]) {
        buckets[minute].anomalies += 1;
        // Keep track of the most severe anomaly in this bucket for rendering
        if (!buckets[minute].anomalyData) {
          buckets[minute].anomalyData = [];
        }
        buckets[minute].anomalyData.push({
          severity: loop.severity,
          score: loop.anomaly_score,
          service: loop.suspected_root_service || "unknown",
          color: SEVERITY_COLOR[loop.severity] || SEVERITY_COLOR.medium
        });
        
        // The scatter point's Y value can just be placed at the log volume, or a fixed height
        buckets[minute].scatterY = buckets[minute].logs;
        buckets[minute].scatterColor = buckets[minute].anomalyData.some((a: any) => a.severity === 'critical' || a.severity === 'high') 
          ? SEVERITY_COLOR.high 
          : buckets[minute].anomalyData.some((a: any) => a.severity === 'medium')
            ? SEVERITY_COLOR.medium
            : SEVERITY_COLOR.low;
      }
    });

    return Object.values(buckets).slice(-20);
  }, [filteredLogs, activeTrackingLoops, filters]);

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      // Find logs and errors from payload
      const data = payload[0].payload;
      return (
        <div className="bg-[#0d1117]/90 backdrop-blur-sm border border-[#21262d] rounded-xl p-4 shadow-xl min-w-[200px]">
          <p className="text-[#e6edf3] font-bold mb-3 text-[13px] border-b border-[#21262d] pb-2">{label} UTC</p>
          <div className="space-y-2 text-xs font-medium">
            <div className="flex justify-between gap-6 items-center">
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-sm bg-[#388bfd]" />
                <span className="text-[#8b949e]">Total Logs</span>
              </div>
              <span className="text-[#e6edf3] font-mono">{data.logs}</span>
            </div>
            <div className="flex justify-between gap-6 items-center">
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-[#d29922]" />
                <span className="text-[#8b949e]">Errors</span>
              </div>
              <span className="text-[#e6edf3] font-mono">{data.errors}</span>
            </div>
          </div>
          {data.anomalyData && data.anomalyData.length > 0 && (
            <div className="mt-4 pt-3 border-t border-[#21262d]">
              <p className="text-[11px] text-[#8b949e] uppercase tracking-wider font-bold mb-2">Anomalies Detected</p>
              <div className="space-y-2.5">
                {data.anomalyData.map((a: any, i: number) => (
                  <div key={i} className="flex flex-col gap-1 bg-[#161b22] p-2 rounded-md border border-[#30363d]">
                    <div className="flex items-center justify-between">
                      <span className="text-[#e6edf3] font-semibold text-[11px]">{a.service}</span>
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase" style={{ background: `${a.color}20`, color: a.color }}>
                        {a.severity}
                      </span>
                    </div>
                    <div className="text-[10px] text-[#7d8590]">Score: <span className="font-mono text-[#c9d1d9]">{a.score.toFixed(3)}</span></div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      );
    }
    return null;
  };

  return (
    <div className="flex flex-col rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d]">
        <div className="flex items-center gap-2">
          <BarChart2 className="w-4 h-4 text-[#388bfd]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Telemetry Stream & Anomalies</span>
        </div>
        <div className="flex items-center gap-4">
          {/* Severity Filter Bar */}
          <div className="flex items-center gap-2 bg-[#0d1117] px-3 py-1.5 rounded-lg border border-[#30363d] shadow-sm">
            <Filter className="w-3.5 h-3.5 text-[#8b949e]" />
            <span className="text-[#8b949e] text-[11px] uppercase font-bold mr-2">Anomalies:</span>
            {[
              { id: 'high', label: 'High', color: SEVERITY_COLOR.high },
              { id: 'medium', label: 'Medium', color: SEVERITY_COLOR.medium },
              { id: 'low', label: 'Low', color: SEVERITY_COLOR.low },
            ].map((f) => {
              const active = filters[f.id as keyof typeof filters];
              return (
                <button
                  key={f.id}
                  onClick={() => toggleFilter(f.id as keyof typeof filters)}
                  className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold transition-all border ${
                    active ? "text-white" : "text-[#8b949e] hover:text-[#c9d1d9]"
                  }`}
                  style={{
                    backgroundColor: active ? f.color : 'transparent',
                    borderColor: active ? f.color : '#30363d',
                  }}
                >
                  {f.label}
                </button>
              );
            })}
          </div>
          <div className="hidden sm:flex gap-4">
            {[
              { color: "#388bfd", label: "Logs" },
              { color: "#d29922", label: "Errors" },
            ].map((item) => (
              <div key={item.label} className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full" style={{ background: item.color }} />
                <span className="text-[#7d8590]" style={{ fontSize: "10px" }}>{item.label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="px-4 pb-4 pt-2">
        <ResponsiveContainer width="100%" height={160}>
          <ComposedChart data={timeSeriesData.length > 0 ? timeSeriesData : [{ time: '00:00', logs: 0, errors: 0, anomalies: 0 }]} margin={{ top: 15, right: 0, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="gradLogs" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#388bfd" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#388bfd" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gradErrors" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#d29922" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#d29922" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid key="grid" strokeDasharray="3 3" stroke="#21262d" vertical={false} />
            <XAxis key="xaxis" dataKey="time" tick={{ fill: "#8b949e", fontSize: 11 }} axisLine={{ stroke: "#30363d" }} tickLine={false} dy={5} />
            <YAxis key="yaxis" tick={{ fill: "#8b949e", fontSize: 11 }} axisLine={false} tickLine={false} dx={-5} />
            <Tooltip
              content={<CustomTooltip />}
              cursor={{ fill: "#21262d", opacity: 0.4 }}
            />
            
            {/* Logs as subtle bars */}
            <Bar dataKey="logs" fill="#388bfd" radius={[2, 2, 0, 0]} barSize={12} opacity={0.8} />
            
            {/* Errors as a distinct line overlay */}
            <Line dataKey="errors" type="monotone" stroke="#d29922" strokeWidth={2} dot={{ r: 3, fill: "#d29922", strokeWidth: 0 }} activeDot={{ r: 5, fill: "#d29922" }} />
            
            {/* Scatter points for anomalies with high contrast */}
            <Scatter 
              dataKey="scatterY" 
              shape={(props: any) => {
                const { cx, cy, payload } = props;
                if (!payload.scatterColor) return <g />;
                return (
                  <g transform={`translate(${cx},${cy - 10})`}>
                    <path d="M 0 -8 L 8 6 L -8 6 Z" fill={payload.scatterColor} className="animate-pulse" />
                    <path d="M 0 -8 L 8 6 L -8 6 Z" fill="none" stroke="#fff" strokeWidth={1.5} opacity={0.8} />
                  </g>
                );
              }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

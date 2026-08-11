import { BarChart2, Filter } from "lucide-react";
import {
  Area,
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
      const data = payload[0].payload;
      return (
        <div className="bg-[#0d1117] border border-[#21262d] rounded-lg p-3 shadow-lg max-w-xs">
          <p className="text-[#7d8590] font-semibold mb-2 text-xs">{label}</p>
          <div className="space-y-1 text-xs">
            <div className="flex justify-between gap-4">
              <span className="text-[#388bfd]">Logs:</span>
              <span className="text-[#e6edf3]">{data.logs}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-[#d29922]">Errors:</span>
              <span className="text-[#e6edf3]">{data.errors}</span>
            </div>
          </div>
          {data.anomalyData && data.anomalyData.length > 0 && (
            <div className="mt-3 pt-2 border-t border-[#21262d]">
              <p className="text-xs text-[#c9d1d9] font-bold mb-1">Detected Anomalies</p>
              <div className="space-y-2">
                {data.anomalyData.map((a: any, i: number) => (
                  <div key={i} className="text-[10px]">
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: a.color }} />
                      <span className="text-[#e6edf3] font-medium">{a.service}</span>
                      <span className="text-[#7d8590] ml-auto">Score: {a.score.toFixed(2)}</span>
                    </div>
                    <span className="px-1.5 py-0.5 rounded uppercase" style={{ background: `${a.color}20`, color: a.color, fontSize: '8px' }}>
                      {a.severity}
                    </span>
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
          <div className="flex items-center gap-2 bg-[#0d1117] px-2.5 py-1 rounded-md border border-[#21262d]">
            <Filter className="w-3 h-3 text-[#7d8590]" />
            <span className="text-[#7d8590] text-[10px] uppercase font-bold mr-1">Anomalies:</span>
            {[
              { id: 'high', label: 'High', color: SEVERITY_COLOR.high },
              { id: 'medium', label: 'Medium', color: SEVERITY_COLOR.medium },
              { id: 'low', label: 'Low', color: SEVERITY_COLOR.low },
            ].map((f) => (
              <label key={f.id} className="flex items-center gap-1 cursor-pointer">
                <input 
                  type="checkbox" 
                  checked={filters[f.id as keyof typeof filters]} 
                  onChange={() => toggleFilter(f.id as keyof typeof filters)}
                  className="w-3 h-3 rounded bg-[#21262d] border-[#30363d] text-blue-500 focus:ring-0 cursor-pointer"
                />
                <span style={{ color: f.color, fontSize: '10px' }}>{f.label}</span>
              </label>
            ))}
          </div>
          <div className="hidden sm:flex gap-3">
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
            <XAxis key="xaxis" dataKey="time" tick={{ fill: "#484f58", fontSize: 10 }} axisLine={{ stroke: "#21262d" }} tickLine={false} />
            <YAxis key="yaxis" tick={{ fill: "#484f58", fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip
              content={<CustomTooltip />}
              cursor={{ stroke: "#484f58", strokeWidth: 1, strokeDasharray: "4 4" }}
            />
            <Area key="logs" type="monotone" dataKey="logs" stroke="#388bfd" strokeWidth={1.5} fill="url(#gradLogs)" dot={false} activeDot={{ r: 4, strokeWidth: 0, fill: "#388bfd" }} />
            <Area key="errors" type="monotone" dataKey="errors" stroke="#d29922" strokeWidth={1.5} fill="url(#gradErrors)" dot={false} activeDot={{ r: 4, strokeWidth: 0, fill: "#d29922" }} />
            
            {/* Scatter points for anomalies */}
            <Scatter 
              dataKey="scatterY" 
              fill="#f85149"
              shape={(props: any) => {
                const { cx, cy, payload } = props;
                if (!payload.scatterColor) return <g />;
                return (
                  <g transform={`translate(${cx},${cy})`}>
                    <circle r={6} fill={payload.scatterColor} fillOpacity={0.2} className="animate-pulse" />
                    <circle r={3} fill={payload.scatterColor} stroke="#0d1117" strokeWidth={1} />
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

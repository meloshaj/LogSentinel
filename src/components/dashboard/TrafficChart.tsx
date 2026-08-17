import { BarChart2, Filter, Activity, AlertTriangle, Flame } from "lucide-react";
import {
  Area,
  Line,
  ComposedChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useLiveLogs } from "../../hooks/useLiveLogs";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";
import { useMemo, useState } from "react";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "#ef4444",
  high: "#f97316",
  medium: "#f59e0b",
  low: "#6366f1",
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
    const windowMinutes = 20;
    const now = new Date();
    now.setSeconds(0, 0);
    const buckets = new Map<string, {
      time: string;
      logs: number;
      errors: number;
      anomalies: number;
      anomalyData: Array<{ severity: string; score: number; service: string; color: string }>;
    }>();

    // Pre-fill the visible window. This keeps the initial chart baseline honest:
    // absent telemetry is represented as zero rather than a fabricated sample.
    for (let index = windowMinutes - 1; index >= 0; index -= 1) {
      const bucketTime = new Date(now.getTime() - index * 60_000);
      const time = bucketTime.toTimeString().slice(0, 5);
      buckets.set(time, { time, logs: 0, errors: 0, anomalies: 0, anomalyData: [] });
    }

    const ensureBucket = (time: string) => {
      const existing = buckets.get(time);
      if (existing) return existing;
      const bucket = { time, logs: 0, errors: 0, anomalies: 0, anomalyData: [] };
      buckets.set(time, bucket);
      return bucket;
    };
    
    filteredLogs.forEach(log => {
      const minute = log.timestamp.split(':').slice(0, 2).join(':');
      const bucket = ensureBucket(minute);
      bucket.logs += 1;
      if (log.level === 'ERROR' || log.level === 'FATAL' || log.level === 'CRITICAL') {
        bucket.errors += 1;
      }
    });

    // Overlay live anomalies onto corresponding minute buckets
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

      const d = (loop as any).created_at ? new Date((loop as any).created_at) : new Date();
      const minute = d.toTimeString().split(':').slice(0, 2).join(':');
      
      const bucket = ensureBucket(minute);
      bucket.anomalies += 1;
      bucket.anomalyData.push({
        severity: loop.severity,
        score: loop.anomaly_score,
        service: loop.suspected_root_service || "unknown",
        color: SEVERITY_COLOR[loop.severity] || SEVERITY_COLOR.high
      });
    });

    return Array.from(buckets.values())
      .sort((a: any, b: any) => a.time.localeCompare(b.time))
      .slice(-20);
  }, [filteredLogs, activeTrackingLoops, filters]);

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div className="bg-[#0d1117]/95 backdrop-blur-md border border-[#21262d] rounded-xl p-3.5 shadow-2xl min-w-[210px]">
          <p className="text-[#e6edf3] font-bold mb-2.5 text-xs border-b border-[#21262d] pb-1.5">{label} UTC</p>
          <div className="space-y-1.5 text-xs font-medium">
            <div className="flex justify-between gap-4 items-center">
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-sm bg-[#6366f1]" />
                <span className="text-[#8b949e]">Log Volume</span>
              </div>
              <span className="text-[#e6edf3] font-mono font-bold">{data.logs}</span>
            </div>
            <div className="flex justify-between gap-4 items-center">
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-[#f59e0b]" />
                <span className="text-[#8b949e]">Errors</span>
              </div>
              <span className="text-[#f59e0b] font-mono font-bold">{data.errors}</span>
            </div>
            <div className="flex justify-between gap-4 items-center">
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-[#ef4444] animate-pulse" />
                <span className="text-[#ef4444]">Anomalies</span>
              </div>
              <span className="text-[#ef4444] font-mono font-bold">{data.anomalies || 0}</span>
            </div>
          </div>
          {data.anomalyData && data.anomalyData.length > 0 && (
            <div className="mt-3 pt-2.5 border-t border-[#21262d]">
              <p className="text-[9px] text-[#8b949e] uppercase tracking-wider font-bold mb-1.5">Detected Anomaly Events</p>
              <div className="space-y-1.5">
                {data.anomalyData.map((a: any, i: number) => (
                  <div key={i} className="flex items-center justify-between bg-[#161b22] px-2 py-1 rounded border border-[#30363d] text-[10px]">
                    <span className="text-[#e6edf3] font-semibold font-mono">{a.service}</span>
                    <span className="px-1.5 py-0.2 rounded font-bold uppercase text-[8px]" style={{ background: `${a.color}20`, color: a.color }}>
                      {a.severity} ({a.score.toFixed(2)})
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
    <div className="flex flex-col rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-[#21262d]">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-[#6366f1]" />
          <span className="text-[#e6edf3] text-[13px] font-bold">Telemetry Stream & Live Anomalies</span>
        </div>
        
        <div className="flex items-center gap-4">
          {/* Severity Filter */}
          <div className="flex items-center gap-1.5 bg-[#0d1117] px-2.5 py-1 rounded-lg border border-[#30363d] shadow-sm">
            <Filter className="w-3 h-3 text-[#8b949e]" />
            <span className="text-[#8b949e] text-[9px] uppercase font-bold mr-1">Severity:</span>
            {[
              { id: 'high', label: 'High', color: "#ef4444" },
              { id: 'medium', label: 'Med', color: "#f59e0b" },
              { id: 'low', label: 'Low', color: "#6366f1" },
            ].map((f) => {
              const active = filters[f.id as keyof typeof filters];
              return (
                <button
                  key={f.id}
                  onClick={() => toggleFilter(f.id as keyof typeof filters)}
                  className={`px-2 py-0.5 rounded-full text-[9px] font-bold transition-all border ${
                    active ? "text-white shadow-sm" : "text-[#8b949e] hover:text-[#c9d1d9]"
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

          {/* Three-Metric Legend */}
          <div className="flex items-center gap-3.5 text-xs">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm bg-[#6366f1]" />
              <span className="text-[#8b949e] text-[11px] font-medium">Log Volume</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-[#f59e0b]" />
              <span className="text-[#8b949e] text-[11px] font-medium">Errors</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-[#ef4444] animate-pulse" />
              <span className="text-[#ef4444] text-[11px] font-bold">Anomalies</span>
            </div>
          </div>
        </div>
      </div>

      <div className="px-4 pb-4 pt-2">
        <ResponsiveContainer width="100%" height={175}>
          <ComposedChart 
            data={timeSeriesData} 
            margin={{ top: 15, right: 10, left: -20, bottom: 0 }}
          >
            <defs>
              <linearGradient id="gradTotalLogs" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366f1" stopOpacity={0.35} />
                <stop offset="95%" stopColor="#6366f1" stopOpacity={0.01} />
              </linearGradient>
              <linearGradient id="gradErrorsGlow" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.30} />
                <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.01} />
              </linearGradient>
            </defs>
            
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
            <XAxis dataKey="time" tick={{ fill: "#8b949e", fontSize: 10 }} axisLine={{ stroke: "#30363d" }} tickLine={false} dy={5} />
            <YAxis 
              tick={{ fill: "#8b949e", fontSize: 10 }} 
              axisLine={false} 
              tickLine={false} 
              dx={-5} 
              domain={[0, 'auto']} 
              allowDataOverflow={false} 
            />
            
            <Tooltip content={<CustomTooltip />} cursor={{ fill: "#21262d", opacity: 0.3 }} />
            
            {/* Graph 1: Total Log Volume Soft Glowing Area */}
            <Area 
              type="monotone" 
              dataKey="logs" 
              name="Logs"
              stroke="#6366f1" 
              strokeWidth={1.8} 
              fill="url(#gradTotalLogs)" 
              dot={false} 
            />

            {/* Graph 2: Errors Rate Amber Line Overlay */}
            <Line 
              dataKey="errors" 
              type="monotone" 
              name="Errors"
              stroke="#f59e0b" 
              strokeWidth={2} 
              dot={{ r: 2.5, fill: "#f59e0b", strokeWidth: 0 }} 
              activeDot={{ r: 4.5, fill: "#f59e0b" }} 
            />
            
            {/* Graph 3: Anomalies High-Contrast Red Line with glowing pulse dots */}
            <Line 
              dataKey="anomalies" 
              type="monotone" 
              name="Anomalies"
              stroke="#ef4444" 
              strokeWidth={2.5} 
              dot={{ r: 3.5, fill: "#ef4444", stroke: "#0d1117", strokeWidth: 1.5 }} 
              activeDot={{ r: 6.0, fill: "#ef4444", stroke: "#ffffff", strokeWidth: 2 }} 
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

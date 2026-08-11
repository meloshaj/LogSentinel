import { Activity, AlertTriangle, CheckCircle, Database, TrendingDown, TrendingUp, EyeOff, Eye } from "lucide-react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { useLiveLogs } from "../../hooks/useLiveLogs";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";
import { useTopology } from "../../hooks/useTopology";

const sparklineData = [
  [12, 19, 14, 21, 15, 18, 24],
  [2, 1, 3, 0, 2, 4, 6],
  [95, 96, 94, 91, 88, 86, 82],
  [8, 8, 9, 9, 10, 9, 8],
];

interface CardProps {
  title: string;
  value: string | number;
  sub: string;
  trend: "up" | "down" | "neutral";
  trendLabel: string;
  icon: React.ElementType;
  iconBg: string;
  iconColor: string;
  accentColor: string;
  sparkIdx: number;
  children?: React.ReactNode;
}

function MetricCard({ title, value, sub, trend, trendLabel, icon: Icon, iconBg, iconColor, accentColor, sparkIdx, children }: CardProps) {
  const TrendIcon = trend === "up" ? TrendingUp : trend === "down" ? TrendingDown : Activity;
  const trendColor =
    trend === "up"
      ? accentColor === "#da3633" || accentColor === "#f85149"
        ? "text-[#f85149]"
        : "text-[#3fb950]"
      : trend === "down"
      ? accentColor === "#da3633" || accentColor === "#f85149"
        ? "text-[#3fb950]"
        : "text-[#f85149]"
      : "text-[#7d8590]";

  const data = sparklineData[sparkIdx].map((v) => ({ v }));

  return (
    <div className="relative flex flex-col gap-3 p-4 rounded-xl bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#21262d] overflow-hidden shadow-sm dark:shadow-none">
      {/* Glow accent */}
      <div
        className="absolute -top-6 -right-6 w-24 h-24 rounded-full opacity-10 blur-2xl pointer-events-none"
        style={{ background: accentColor }}
      />

      <div className="flex items-start justify-between">
        <div className={`flex items-center justify-center w-9 h-9 rounded-lg`} style={{ background: iconBg }}>
          <Icon className="w-4 h-4" style={{ color: iconColor }} />
        </div>
        <div className={`flex items-center gap-1 ${trendColor}`}>
          <TrendIcon className="w-3.5 h-3.5" />
          <span style={{ fontSize: "11px", fontWeight: 500 }}>{trendLabel}</span>
        </div>
      </div>

      <div className="flex items-end justify-between">
        <div>
          <div className="text-slate-900 dark:text-[#e6edf3]" style={{ fontSize: "26px", fontWeight: 700, lineHeight: 1.1 }}>{value}</div>
          <div className="text-slate-500 dark:text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>{title}</div>
        </div>
        {children}
      </div>

      <div className="flex items-end gap-2 mt-auto">
        <div style={{ width: "100%", height: 32, minWidth: 0 }}>
          <ResponsiveContainer width="100%" height={32}>
            <AreaChart data={data} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`ls-spark-grad-${sparkIdx}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={accentColor} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={accentColor} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                key="v"
                type="monotone"
                dataKey="v"
                stroke={accentColor}
                strokeWidth={1.5}
                fill={`url(#ls-spark-grad-${sparkIdx})`}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="text-slate-500 dark:text-[#7d8590] pb-1 shrink-0" style={{ fontSize: "11px" }}>{sub}</div>
      </div>
    </div>
  );
}

export function MetricCards({ showLowSeverity = true, onToggleLowSeverity }: { showLowSeverity?: boolean, onToggleLowSeverity?: () => void }) {
  const { totalLogCount } = useLiveLogs();
  const { activeTrackingLoops } = useTelemetryStream();
  const { topology } = useTopology();

  const totalLogs = totalLogCount;
  
  // Severity breakdown
  let criticalCount = 0;
  let highCount = 0;
  let mediumCount = 0;
  let lowCount = 0;
  
  activeTrackingLoops.forEach((loop) => {
    if (loop.severity === "critical") criticalCount++;
    else if (loop.severity === "high") highCount++;
    else if (loop.severity === "medium") mediumCount++;
    else lowCount++;
  });
  
  const numAnomalies = showLowSeverity ? activeTrackingLoops.length : (activeTrackingLoops.length - lowCount);
  
  // Compute how many services are currently affected by anomalies
  const affectedServices = new Set<string>();
  activeTrackingLoops.forEach(loop => {
    if (!showLowSeverity && loop.severity === "low") return;
    if (loop.suspected_root_service) {
      affectedServices.add(loop.suspected_root_service);
    }
    if (loop.blast_radius) {
      loop.blast_radius.forEach(node => {
        if (node.service_name) {
          affectedServices.add(node.service_name);
        }
      });
    }
  });
  
  const totalServices = topology?.node_count || 0;
  const numDegraded = affectedServices.size;
  const healthScore = totalServices > 0 
    ? Math.max(0, 100 - Math.round((numDegraded / totalServices) * 100))
    : 100;
  
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <MetricCard
        title="Logs Processed"
        value={totalLogs.toLocaleString()}
        sub="session total"
        trend="neutral"
        trendLabel="Live"
        icon={Database}
        iconBg="rgba(31,111,235,0.15)"
        iconColor="#388bfd"
        accentColor="#388bfd"
        sparkIdx={0}
      />
      <MetricCard
        title="Active Anomalies"
        value={numAnomalies}
        sub={`${numAnomalies} tracked`}
        trend={numAnomalies > 0 ? "up" : "neutral"}
        trendLabel={numAnomalies > 0 ? "Detected" : "None"}
        icon={AlertTriangle}
        iconBg="rgba(218,54,51,0.15)"
        iconColor="#f85149"
        accentColor="#da3633"
        sparkIdx={1}
      >
        <div className="flex flex-col gap-1 pr-1 border-l border-[#21262d] pl-3 py-0.5">
          <div className="flex items-center gap-1.5" title="High / Critical">
            <span className="w-1.5 h-1.5 rounded-full bg-[#f85149]" />
            <span className="text-[#e6edf3] text-[10px] font-mono leading-none">{criticalCount + highCount}</span>
          </div>
          <div className="flex items-center gap-1.5" title="Medium">
            <span className="w-1.5 h-1.5 rounded-full bg-[#d29922]" />
            <span className="text-[#e6edf3] text-[10px] font-mono leading-none">{mediumCount}</span>
          </div>
          <div 
            className={`flex items-center gap-1.5 rounded px-1 -ml-1 py-0.5 cursor-pointer transition-colors ${!showLowSeverity ? "opacity-50" : "hover:bg-[#21262d]"}`}
            title="Low Severity (Click to toggle)"
            onClick={(e) => {
              e.stopPropagation();
              onToggleLowSeverity?.();
            }}
          >
            {showLowSeverity ? <span className="w-1.5 h-1.5 rounded-full bg-[#7d8590]" /> : <EyeOff className="w-2 h-2 text-[#7d8590]" />}
            <span className="text-[#e6edf3] text-[10px] font-mono leading-none">{lowCount}</span>
          </div>
        </div>
      </MetricCard>
      <MetricCard
        title="Health Score"
        value={`${healthScore}%`}
        sub={numAnomalies > 0 ? "Degraded" : "Healthy"}
        trend={numAnomalies > 0 ? "down" : "neutral"}
        trendLabel={numAnomalies > 0 ? `-${100 - healthScore}pts` : "Stable"}
        icon={Activity}
        iconBg="rgba(210,153,34,0.15)"
        iconColor="#d29922"
        accentColor="#d29922"
        sparkIdx={2}
      />
      <MetricCard
        title="Services"
        value={`${totalServices - numDegraded} / ${Math.max(totalServices, 1)}`}
        sub={numDegraded > 0 ? `${numDegraded} degraded` : "All nominal"}
        trend={numDegraded > 0 ? "down" : "neutral"}
        trendLabel={numDegraded > 0 ? `${numDegraded} failing` : "All passing"}
        icon={CheckCircle}
        iconBg="rgba(63,185,80,0.12)"
        iconColor="#3fb950"
        accentColor="#3fb950"
        sparkIdx={3}
      />
    </div>
  );
}


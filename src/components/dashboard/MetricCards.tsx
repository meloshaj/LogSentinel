import { Activity, AlertTriangle, CheckCircle, Database, TrendingDown, TrendingUp } from "lucide-react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { useLiveLogs } from "../../hooks/useLiveLogs";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";

const sparklineData = [
  [12, 19, 14, 21, 15, 18, 24],
  [2, 1, 3, 0, 2, 4, 6],
  [95, 96, 94, 91, 88, 86, 82],
  [8, 8, 9, 9, 10, 9, 8],
];

interface CardProps {
  title: string;
  value: string;
  sub: string;
  trend: "up" | "down" | "neutral";
  trendLabel: string;
  icon: React.ElementType;
  iconBg: string;
  iconColor: string;
  accentColor: string;
  sparkIdx: number;
}

function MetricCard({ title, value, sub, trend, trendLabel, icon: Icon, iconBg, iconColor, accentColor, sparkIdx }: CardProps) {
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
    <div className="relative flex flex-col gap-3 p-4 rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      {/* Glow accent */}
      <div
        className="absolute -top-6 -right-6 w-24 h-24 rounded-full opacity-10 blur-2xl"
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

      <div>
        <div className="text-[#e6edf3]" style={{ fontSize: "26px", fontWeight: 700, lineHeight: 1.1 }}>{value}</div>
        <div className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>{title}</div>
      </div>

      <div className="flex items-end gap-2">
        <div style={{ width: "100%", height: 40, minWidth: 0 }}>
          <ResponsiveContainer width="100%" height={40}>
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
        <div className="text-[#7d8590] pb-1 shrink-0" style={{ fontSize: "11px" }}>{sub}</div>
      </div>
    </div>
  );
}

export function MetricCards() {
  const { filteredLogs } = useLiveLogs();
  const { activeTrackingLoops } = useTelemetryStream();

  const totalLogs = filteredLogs.length;
  const numAnomalies = activeTrackingLoops.length;
  
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <MetricCard
        title="Logs Processed"
        value={totalLogs.toString()}
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
        value={numAnomalies.toString()}
        sub={`${numAnomalies} critical`}
        trend={numAnomalies > 0 ? "up" : "neutral"}
        trendLabel={numAnomalies > 0 ? "Detected" : "None"}
        icon={AlertTriangle}
        iconBg="rgba(218,54,51,0.15)"
        iconColor="#f85149"
        accentColor="#da3633"
        sparkIdx={1}
      />
      <MetricCard
        title="Health Score"
        value={numAnomalies > 0 ? "82%" : "100%"}
        sub={numAnomalies > 0 ? "Degraded" : "Healthy"}
        trend={numAnomalies > 0 ? "down" : "neutral"}
        trendLabel={numAnomalies > 0 ? "-18pts" : "Stable"}
        icon={Activity}
        iconBg="rgba(210,153,34,0.15)"
        iconColor="#d29922"
        accentColor="#d29922"
        sparkIdx={2}
      />
      <MetricCard
        title="Services"
        value={numAnomalies > 0 ? "8 / 10" : "10 / 10"}
        sub={numAnomalies > 0 ? "2 degraded" : "All nominal"}
        trend={numAnomalies > 0 ? "down" : "neutral"}
        trendLabel={numAnomalies > 0 ? "2 failing" : "All passing"}
        icon={CheckCircle}
        iconBg="rgba(63,185,80,0.12)"
        iconColor="#3fb950"
        accentColor="#3fb950"
        sparkIdx={3}
      />
    </div>
  );
}

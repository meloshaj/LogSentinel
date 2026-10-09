import React, { useMemo } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle,
  Database,
  TrendingDown,
  TrendingUp,
  EyeOff,
} from "lucide-react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { useLiveLogs } from "../../hooks/useLiveLogs";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";
import { useTopology } from "../../hooks/useTopology";
import {
  displayOperationalStatus,
  formatOperationalValue,
} from "../common/OperationalState";

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
  sparkData: number[];
  children?: React.ReactNode;
}

function MetricCard({
  title,
  value,
  sub,
  trend,
  trendLabel,
  icon: Icon,
  iconBg,
  iconColor,
  accentColor,
  sparkData,
  children,
}: CardProps) {
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

  return (
    <div className="relative flex flex-col gap-3 overflow-hidden rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-[#21262d] dark:bg-[#161b22] dark:shadow-none">
      <div
        className="pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full opacity-10 blur-2xl"
        style={{ background: accentColor }}
      />
      <div className="flex items-start justify-between">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg" style={{ background: iconBg }}>
          <Icon className="h-4 w-4" style={{ color: iconColor }} />
        </div>
        <div className={`flex items-center gap-1 ${trendColor}`}>
          <TrendIcon className="h-4 w-4" />
          <span style={{ fontSize: "13px", fontWeight: 700 }}>{trendLabel}</span>
        </div>
      </div>

      <div className="flex items-end justify-between">
        <div>
          <div className="text-slate-900 dark:text-[#e6edf3]" style={{ fontSize: "26px", fontWeight: 700, lineHeight: 1.1 }}>
            {value}
          </div>
          <div className="mt-0.5 text-slate-500 dark:text-[#7d8590]" style={{ fontSize: "12px" }}>
            {title}
          </div>
        </div>
        {children}
      </div>

      <div className="mt-auto flex items-end gap-2">
        <div style={{ width: "100%", height: 32, minWidth: 0 }}>
          {sparkData.length > 0 ? (
            <ResponsiveContainer width="100%" height={32}>
              <AreaChart data={sparkData.map((v) => ({ v }))} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id={`ls-spark-grad-${title.replace(/\s+/g, "")}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={accentColor} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={accentColor} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <Area
                  type="monotone"
                  dataKey="v"
                  stroke={accentColor}
                  strokeWidth={1.5}
                  fill={`url(#ls-spark-grad-${title.replace(/\s+/g, "")})`}
                  dot={false}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : null}
        </div>
        <div className="shrink-0 pb-1 text-slate-500 dark:text-[#7d8590]" style={{ fontSize: "11px" }}>
          {sub}
        </div>
      </div>
    </div>
  );
}

function statusLabel(status: ReturnType<typeof displayOperationalStatus>) {
  return status === "available" ? "Current" : status === "empty" ? "No data" : status === "stale" ? "Stale" : status === "loading" ? "Loading" : "Unavailable";
}

export function MetricCards({
  showLowSeverity = true,
  onToggleLowSeverity,
}: {
  showLowSeverity?: boolean;
  onToggleLowSeverity?: () => void;
}) {
  const { totalLogCount, filteredLogs, dataState: logState, connectionState } = useLiveLogs();
  const { activeTrackingLoops, trackingLoopsDataState } = useTelemetryStream();
  const { topology, dataState: topologyState } = useTopology();

  const logStatus = displayOperationalStatus(logState, connectionState);
  const loopStatus = displayOperationalStatus(trackingLoopsDataState, connectionState);
  const topologyStatus = topologyState.status;
  const logsKnown = logStatus === "available" || logStatus === "empty" || logStatus === "stale";
  const loopsKnown = loopStatus === "available" || loopStatus === "empty" || loopStatus === "stale";
  const topologyKnown = topologyStatus === "available" || topologyStatus === "empty" || topologyStatus === "stale";

  const { logsSpark, errorsSpark } = useMemo(() => {
    const logs = Array.from({ length: 10 }, () => 0);
    const errors = Array.from({ length: 10 }, () => 0);
    if (!logsKnown) return { logsSpark: [], errorsSpark: [] };

    const now = Date.now();
    filteredLogs.forEach((log) => {
      const timestamp = new Date(log.timestamp).getTime();
      if (!Number.isFinite(timestamp)) return;
      const diffMin = Math.floor((now - timestamp) / 60000);
      if (diffMin >= 0 && diffMin < 10) {
        logs[9 - diffMin] += 1;
        if (["ERROR", "FATAL", "CRITICAL"].includes(log.level)) errors[9 - diffMin] += 1;
      }
    });
    return { logsSpark: filteredLogs.length > 0 ? logs : [], errorsSpark: filteredLogs.length > 0 ? errors : [] };
  }, [filteredLogs, logsKnown]);

  const severityCounts = useMemo(() => {
    const counts = { critical: 0, high: 0, medium: 0, low: 0 };
    activeTrackingLoops.forEach((loop) => {
      if (loop.severity === "critical") counts.critical += 1;
      else if (loop.severity === "high") counts.high += 1;
      else if (loop.severity === "medium") counts.medium += 1;
      else if (loop.severity === "low") counts.low += 1;
    });
    return counts;
  }, [activeTrackingLoops]);

  const visibleAnomalies = showLowSeverity
    ? activeTrackingLoops
    : activeTrackingLoops.filter((loop) => loop.severity !== "low");
  const numAnomalies = loopsKnown ? visibleAnomalies.length : null;
  const nodes = topology?.nodes ?? [];
  const healthyServices = nodes.filter((node) => node.status === "healthy").length;
  const degradedServices = nodes.filter((node) => node.status !== "healthy").length;
  const healthScore = topologyKnown && nodes.length > 0
    ? Math.max(0, Math.round((healthyServices / nodes.length) * 100))
    : null;

  const logsValue = formatOperationalValue(logState, totalLogCount.toLocaleString(), connectionState);
  const anomalyValue = numAnomalies === null ? statusLabel(loopStatus) : numAnomalies;
  const servicesValue = topologyKnown ? `${healthyServices} / ${nodes.length}` : statusLabel(topologyStatus);
  const scoreValue = healthScore === null ? statusLabel(topologyStatus) : `${healthScore}%`;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <MetricCard
        title="Logs Processed"
        value={logsValue}
        sub={statusLabel(logStatus) === "Current" ? "session total" : statusLabel(logStatus)}
        trend="neutral"
        trendLabel={statusLabel(logStatus)}
        icon={Database}
        iconBg="rgba(31,111,235,0.15)"
        iconColor="#388bfd"
        accentColor="#388bfd"
        sparkData={logsSpark}
      />
      <MetricCard
        title="Active Anomalies"
        value={anomalyValue}
        sub={numAnomalies === null ? statusLabel(loopStatus) : numAnomalies === 0 ? "No anomalies" : `${numAnomalies} tracked`}
        trend={numAnomalies !== null && numAnomalies > 0 ? "up" : "neutral"}
        trendLabel={numAnomalies === null ? statusLabel(loopStatus) : numAnomalies > 0 ? "Detected" : "No data"}
        icon={AlertTriangle}
        iconBg="rgba(218,54,51,0.15)"
        iconColor="#f85149"
        accentColor="#da3633"
        sparkData={errorsSpark}
      >
        {loopsKnown ? (
          <div className="flex flex-col gap-1 border-l border-[#21262d] pl-3 pr-1 py-0.5">
            <div className="flex items-center gap-1.5" title="High / Critical">
              <span className="h-1.5 w-1.5 rounded-full bg-[#f85149]" />
              <span className="font-mono text-[10px] leading-none text-[#e6edf3]">{severityCounts.critical + severityCounts.high}</span>
            </div>
            <div className="flex items-center gap-1.5" title="Medium">
              <span className="h-1.5 w-1.5 rounded-full bg-[#d29922]" />
              <span className="font-mono text-[10px] leading-none text-[#e6edf3]">{severityCounts.medium}</span>
            </div>
            <button
              type="button"
              className={`-ml-1 flex items-center gap-1.5 rounded px-1 py-0.5 transition-colors ${!showLowSeverity ? "opacity-50" : "hover:bg-[#21262d]"}`}
              title="Toggle low severity"
              aria-label="Toggle low severity anomalies"
              onClick={(event) => {
                event.stopPropagation();
                onToggleLowSeverity?.();
              }}
            >
              {showLowSeverity ? <span className="h-1.5 w-1.5 rounded-full bg-[#7d8590]" /> : <EyeOff className="h-2 w-2 text-[#7d8590]" />}
              <span className="font-mono text-[10px] leading-none text-[#e6edf3]">{severityCounts.low}</span>
            </button>
          </div>
        ) : null}
      </MetricCard>
      <MetricCard
        title="Health Score"
        value={scoreValue}
        sub={healthScore === null ? statusLabel(topologyStatus) : `${healthyServices} of ${nodes.length} reported healthy`}
        trend={healthScore !== null && healthScore < 100 ? "down" : "neutral"}
        trendLabel={healthScore === null ? statusLabel(topologyStatus) : degradedServices > 0 ? `${degradedServices} degraded` : "Current"}
        icon={Activity}
        iconBg="rgba(210,153,34,0.15)"
        iconColor="#d29922"
        accentColor="#d29922"
        sparkData={[]}
      />
      <MetricCard
        title="Services"
        value={servicesValue}
        sub={topologyKnown ? (nodes.length === 0 ? "No topology returned" : degradedServices > 0 ? `${degradedServices} degraded` : "All reported healthy") : statusLabel(topologyStatus)}
        trend={degradedServices > 0 ? "down" : "neutral"}
        trendLabel={topologyKnown ? (nodes.length === 0 ? "No data" : degradedServices > 0 ? `${degradedServices} degraded` : "Current") : statusLabel(topologyStatus)}
        icon={CheckCircle}
        iconBg="rgba(63,185,80,0.12)"
        iconColor="#3fb950"
        accentColor="#3fb950"
        sparkData={[]}
      />
    </div>
  );
}

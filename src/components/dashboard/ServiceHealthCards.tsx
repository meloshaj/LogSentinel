import React, { useMemo } from "react";
import { useLiveLogs } from "../../hooks/useLiveLogs";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";
import { Server, Database, Zap, Layers, Share2, Activity, Clock, AlertCircle, CheckCircle2, Flame, ShieldAlert } from "lucide-react";

interface ServiceMetricData {
  name: string;
  type: string;
  status: "healthy" | "degraded" | "critical";
  p99Latency: number;
  errorRate: number;
  templateCount: number;
  throughput: number;
  healthScore: number;
  activeAnomalies: number;
}

const SERVICE_ICONS: Record<string, React.ElementType> = {
  "api-gateway": Share2,
  "auth-service": Server,
  "order-service": Server,
  "payment-gateway": Zap,
  "postgres-db": Database,
};

const DEFAULT_SERVICES = [
  "api-gateway",
  "auth-service",
  "order-service",
  "payment-gateway",
  "postgres-db",
];

export function ServiceHealthCards() {
  const { filteredLogs } = useLiveLogs();
  const { activeTrackingLoops } = useTelemetryStream();

  const servicesData: ServiceMetricData[] = useMemo(() => {
    // 1. Map active anomalies by service
    const anomalyMap = new Map<string, { count: number; severity: string; isRoot: boolean }>();
    activeTrackingLoops.forEach((loop) => {
      if (loop.suspected_root_service) {
        const cur = anomalyMap.get(loop.suspected_root_service) || { count: 0, severity: loop.severity, isRoot: true };
        cur.count += 1;
        if (loop.severity === "critical" || loop.severity === "high") cur.severity = "critical";
        anomalyMap.set(loop.suspected_root_service, cur);
      }
      if (Array.isArray(loop.blast_radius)) {
        loop.blast_radius.forEach((n) => {
          if (n.service_name) {
            const cur = anomalyMap.get(n.service_name) || { count: 0, severity: loop.severity, isRoot: n.impact_classification === "root" };
            cur.count += 1;
            anomalyMap.set(n.service_name, cur);
          }
        });
      }
    });

    // 2. Aggregate logs per service
    const serviceStats: Record<string, { total: number; errors: number; latencies: number[]; templates: Set<string> }> = {};
    DEFAULT_SERVICES.forEach(s => {
      serviceStats[s] = { total: 0, errors: 0, latencies: [], templates: new Set() };
    });

    filteredLogs.forEach((log) => {
      const svc = log.service || "unknown";
      if (!serviceStats[svc]) {
        serviceStats[svc] = { total: 0, errors: 0, latencies: [], templates: new Set() };
      }
      serviceStats[svc].total += 1;
      if (log.level === "ERROR" || log.level === "FATAL" || log.level === "CRITICAL") {
        serviceStats[svc].errors += 1;
      }
      if (log.latency_ms !== undefined) {
        serviceStats[svc].latencies.push(log.latency_ms);
      }
      if (log.template_id) {
        serviceStats[svc].templates.add(log.template_id);
      }
    });

    return Object.entries(serviceStats).map(([name, stat]) => {
      const anomalyInfo = anomalyMap.get(name);
      const errorRate = stat.total > 0 ? (stat.errors / stat.total) * 100 : 0;
      
      let p99 = 12;
      if (stat.latencies.length >= 2) {
        stat.latencies.sort((a, b) => a - b);
        p99 = stat.latencies[Math.floor(stat.latencies.length * 0.99)] ?? 12;
      } else if (stat.latencies.length === 1) {
        p99 = stat.latencies[0];
      }

      // Calculate health score (0-100)
      let healthScore = 100;
      if (anomalyInfo?.severity === "critical" || errorRate > 15 || p99 > 3000) {
        healthScore = Math.max(10, Math.round(100 - errorRate * 2 - (anomalyInfo?.count || 1) * 20));
      } else if (anomalyInfo?.severity === "medium" || errorRate > 5 || p99 > 500) {
        healthScore = Math.max(55, Math.round(100 - errorRate * 3 - (anomalyInfo?.count || 1) * 10));
      }

      let status: "healthy" | "degraded" | "critical" = "healthy";
      if (healthScore < 50 || anomalyInfo?.severity === "critical" || errorRate > 20) {
        status = "critical";
      } else if (healthScore < 85 || anomalyInfo || errorRate > 3) {
        status = "degraded";
      }

      const throughput = Math.max(0.1, Number((stat.total / 30).toFixed(1))); // ~logs/sec in 30s rolling
      const templateCount = stat.templates.size > 0 ? stat.templates.size : (name === "postgres-db" ? 8 : 12);

      let serviceType = "service";
      if (name.includes("db") || name.includes("postgres")) serviceType = "database";
      else if (name.includes("gateway")) serviceType = "gateway";
      else if (name.includes("cache")) serviceType = "cache";

      return {
        name,
        type: serviceType,
        status,
        p99Latency: Math.round(p99),
        errorRate: Number(errorRate.toFixed(1)),
        templateCount,
        throughput,
        healthScore,
        activeAnomalies: anomalyInfo?.count || 0,
      };
    });
  }, [filteredLogs, activeTrackingLoops]);

  return (
    <div className="rounded-xl bg-[#161b22] border border-[#21262d] p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3.5 pb-2.5 border-b border-[#21262d]">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-[#388bfd]" />
          <h3 className="text-[#e6edf3] text-sm font-bold">System Health & Service Matrix</h3>
          <span className="px-2 py-0.5 rounded-full bg-[#388bfd]/10 text-[#388bfd] text-[10px] font-bold">
            {servicesData.length} Monitored
          </span>
        </div>
        <div className="flex items-center gap-3 text-[11px]">
          <span className="flex items-center gap-1 text-[#3fb950]">
            <span className="w-2 h-2 rounded-full bg-[#3fb950]" />
            {servicesData.filter(s => s.status === "healthy").length} Healthy
          </span>
          <span className="flex items-center gap-1 text-[#d29922]">
            <span className="w-2 h-2 rounded-full bg-[#d29922]" />
            {servicesData.filter(s => s.status === "degraded").length} Degraded
          </span>
          <span className="flex items-center gap-1 text-[#f85149]">
            <span className="w-2 h-2 rounded-full bg-[#f85149] animate-pulse" />
            {servicesData.filter(s => s.status === "critical").length} Critical
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
        {servicesData.map((svc) => {
          const Icon = SERVICE_ICONS[svc.name] || Server;
          const isCrit = svc.status === "critical";
          const isDeg = svc.status === "degraded";
          const statusColor = isCrit ? "#f85149" : isDeg ? "#d29922" : "#3fb950";
          const statusBg = isCrit ? "bg-[#f85149]/15 text-[#f85149] border-[#f85149]/40" : isDeg ? "bg-[#d29922]/15 text-[#d29922] border-[#d29922]/40" : "bg-[#3fb950]/15 text-[#3fb950] border-[#3fb950]/40";

          return (
            <div
              key={svc.name}
              className={`
                relative flex flex-col justify-between p-3.5 rounded-xl bg-[#0d1117] border transition-all duration-200
                ${isCrit ? "border-[#f85149]/60 shadow-[0_0_15px_rgba(248,81,73,0.15)]" : isDeg ? "border-[#d29922]/50 shadow-[0_0_12px_rgba(210,153,34,0.1)]" : "border-[#21262d] hover:border-[#30363d]"}
              `}
            >
              {/* Header */}
              <div>
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <div className={`p-1.5 rounded-lg border ${isCrit ? "bg-[#f85149]/20 border-[#f85149]/40 text-[#f85149]" : isDeg ? "bg-[#d29922]/20 border-[#d29922]/40 text-[#d29922]" : "bg-[#21262d] border-[#30363d] text-[#388bfd]"}`}>
                      <Icon className="w-3.5 h-3.5" />
                    </div>
                    <div className="min-w-0">
                      <h4 className="text-[#e6edf3] text-xs font-bold font-mono truncate" title={svc.name}>
                        {svc.name}
                      </h4>
                      <span className="text-[#7d8590] text-[9px] uppercase font-semibold">{svc.type}</span>
                    </div>
                  </div>
                  <span className={`px-1.5 py-0.5 rounded text-[8px] font-bold uppercase tracking-wider border shrink-0 ${statusBg}`}>
                    {svc.status}
                  </span>
                </div>

                {/* Health Meter Bar */}
                <div className="mt-2 mb-3">
                  <div className="flex justify-between items-center text-[10px] mb-1">
                    <span className="text-[#8b949e] font-medium">Health Score</span>
                    <span className="font-mono font-bold" style={{ color: statusColor }}>{svc.healthScore}%</span>
                  </div>
                  <div className="w-full h-1.5 rounded-full bg-[#21262d] overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${svc.healthScore}%`, backgroundColor: statusColor }}
                    />
                  </div>
                </div>
              </div>

              {/* 4-Metric Grid */}
              <div className="grid grid-cols-2 gap-1.5 pt-2 border-t border-[#21262d]/80 text-[10px]">
                <div className="flex flex-col bg-[#161b22] px-2 py-1 rounded border border-[#21262d]">
                  <span className="text-[#7d8590] text-[8px] uppercase font-bold">P99 Latency</span>
                  <span className={`font-mono font-bold mt-0.5 ${svc.p99Latency > 1000 ? "text-[#f85149]" : "text-[#e6edf3]"}`}>
                    {svc.p99Latency}ms
                  </span>
                </div>
                <div className="flex flex-col bg-[#161b22] px-2 py-1 rounded border border-[#21262d]">
                  <span className="text-[#7d8590] text-[8px] uppercase font-bold">Error Rate</span>
                  <span className={`font-mono font-bold mt-0.5 ${svc.errorRate > 5 ? "text-[#f85149]" : svc.errorRate > 0 ? "text-[#d29922]" : "text-[#3fb950]"}`}>
                    {svc.errorRate}%
                  </span>
                </div>
                <div className="flex flex-col bg-[#161b22] px-2 py-1 rounded border border-[#21262d]">
                  <span className="text-[#7d8590] text-[8px] uppercase font-bold">Templates</span>
                  <span className="font-mono font-bold text-[#c9d1d9] mt-0.5">
                    {svc.templateCount} mined
                  </span>
                </div>
                <div className="flex flex-col bg-[#161b22] px-2 py-1 rounded border border-[#21262d]">
                  <span className="text-[#7d8590] text-[8px] uppercase font-bold">Throughput</span>
                  <span className="font-mono font-bold text-[#388bfd] mt-0.5">
                    {svc.throughput}/s
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

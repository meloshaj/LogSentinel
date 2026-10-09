import type { ServiceAnomaly } from "../types/monitoring";
import type { LogEntry } from "../types/monitoring";
import type { TrackingLoopEvent } from "../providers/TelemetryProvider";

export type ServiceMetric = {
  total: number;
  errors: number;
  totalLatency: number;
  countLatency: number;
};

export function buildServiceMetrics(logs: LogEntry[]): Map<string, ServiceMetric> {
  const map = new Map<string, ServiceMetric>();
  logs.forEach((log) => {
    const current = map.get(log.service) ?? {
      total: 0,
      errors: 0,
      totalLatency: 0,
      countLatency: 0,
    };
    current.total += 1;
    if (["ERROR", "FATAL", "CRITICAL"].includes(log.level)) current.errors += 1;
    if (typeof log.latency_ms === "number" && Number.isFinite(log.latency_ms)) {
      current.totalLatency += log.latency_ms;
      current.countLatency += 1;
    }
    map.set(log.service, current);
  });
  return map;
}

/** Build the same de-duplicated service records shown by the Anomalies page. */
export function deriveAnomalyRecords(
  trackingLoops: TrackingLoopEvent[],
  serviceMetrics: Map<string, ServiceMetric>,
): ServiceAnomaly[] {
  const list: ServiceAnomaly[] = [];
  const seenServices = new Set<string>();

  trackingLoops.forEach((loop) => {
    const status: ServiceAnomaly["status"] =
      loop.severity === "critical" || loop.severity === "high"
        ? "Critical"
        : loop.severity === "medium"
          ? "Warning"
          : "Low";
    const nodes = loop.blast_radius ?? [];
    const candidates =
      nodes.length > 0
        ? nodes.map((node) => ({
            service: node.service_name,
            score: node.impact_score > 1 ? node.impact_score / 100 : node.impact_score,
            explanation: `Topological impact '${node.impact_classification}' in tracking loop ${loop.window_id.slice(0, 8)}.`,
          }))
        : loop.suspected_root_service
          ? [
              {
                service: loop.suspected_root_service,
                score: loop.anomaly_score > 1 ? loop.anomaly_score / 100 : loop.anomaly_score,
                explanation: `Backend tracking loop ${loop.window_id.slice(0, 8)} reported an anomaly.`,
              },
            ]
          : [];

    candidates.forEach((candidate) => {
      if (!candidate.service || seenServices.has(candidate.service)) return;
      seenServices.add(candidate.service);
      const metrics = serviceMetrics.get(candidate.service);
      list.push({
        id: `${loop.window_id}-${candidate.service}`,
        name: candidate.service,
        score: Math.min(1, Math.max(0, candidate.score)),
        status,
        explanation: candidate.explanation,
        errorRate:
          metrics && metrics.total > 0
            ? Number(((metrics.errors / metrics.total) * 100).toFixed(1))
            : null,
        latency:
          metrics && metrics.countLatency > 0
            ? Math.round(metrics.totalLatency / metrics.countLatency)
            : null,
        detectedAt: loop.created_at,
      });
    });
  });

  return list.sort((a, b) => b.score - a.score);
}

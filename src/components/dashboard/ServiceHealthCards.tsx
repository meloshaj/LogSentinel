import React, { useMemo } from "react";
import { Activity, Database, Server, Share2, Zap } from "lucide-react";
import { useLiveLogs } from "../../hooks/useLiveLogs";
import { useTopology } from "../../hooks/useTopology";
import { useWorkerHealth, type WorkerRole } from "../../hooks/useWorkerHealth";
import { displayOperationalStatus, OperationalStateNotice } from "../common/OperationalState";
import type { NodeStatus, TopologyNode } from "../../types/topology";

const SERVICE_ICONS: Record<string, React.ElementType> = {
  "api-gateway": Share2,
  "auth-service": Server,
  "payment-gateway": Zap,
};

const STATUS_STYLE: Record<NodeStatus, { color: string; badge: string; border: string }> = {
  healthy: {
    color: "#3fb950",
    badge: "bg-[#3fb950]/15 text-[#3fb950] border-[#3fb950]/40",
    border: "border-[#21262d]",
  },
  degraded: {
    color: "#d29922",
    badge: "bg-[#d29922]/15 text-[#d29922] border-[#d29922]/40",
    border: "border-[#d29922]/50",
  },
  critical: {
    color: "#f85149",
    badge: "bg-[#f85149]/15 text-[#f85149] border-[#f85149]/40",
    border: "border-[#f85149]/60",
  },
};

function metric(value: number | undefined, suffix = "") {
  return typeof value === "number" && Number.isFinite(value) ? `${value}${suffix}` : "Unavailable";
}

function ServiceCard({ node }: { node: TopologyNode }) {
  const Icon = SERVICE_ICONS[node.name] ?? (node.type === "database" ? Database : Server);
  const style = STATUS_STYLE[node.status];
  const metrics = node.metrics;

  return (
    <div className={`relative flex flex-col justify-between rounded-xl border bg-[#0d1117] p-3.5 ${style.border}`}>
      <div>
        <div className="mb-2 flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <div className="rounded-lg border border-[#30363d] bg-[#21262d] p-1.5" style={{ color: style.color }}>
              <Icon className="h-3.5 w-3.5" />
            </div>
            <div className="min-w-0">
              <h4 className="truncate font-mono text-xs font-bold text-[#e6edf3]" title={node.name}>{node.name}</h4>
              <span className="text-[9px] font-semibold uppercase text-[#7d8590]">{node.type}</span>
            </div>
          </div>
          <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wider ${style.badge}`}>
            {node.status}
          </span>
        </div>

        <div className="mb-3 mt-2 flex items-center justify-between text-[10px]">
          <span className="font-medium text-[#8b949e]">Authoritative service state</span>
          <span className="font-mono font-bold uppercase" style={{ color: style.color }}>{node.status}</span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1.5 border-t border-[#21262d]/80 pt-2 text-[10px]">
        <div className="flex flex-col rounded border border-[#21262d] bg-[#161b22] px-2 py-1">
          <span className="text-[8px] font-bold uppercase text-[#7d8590]">P95 Latency</span>
          <span className="mt-0.5 font-mono font-bold text-[#e6edf3]">{metric(metrics.latency_p95_ms, "ms")}</span>
        </div>
        <div className="flex flex-col rounded border border-[#21262d] bg-[#161b22] px-2 py-1">
          <span className="text-[8px] font-bold uppercase text-[#7d8590]">Error Rate</span>
          <span className="mt-0.5 font-mono font-bold text-[#e6edf3]">{metric(metrics.error_rate_pct, "%")}</span>
        </div>
        <div className="flex flex-col rounded border border-[#21262d] bg-[#161b22] px-2 py-1">
          <span className="text-[8px] font-bold uppercase text-[#7d8590]">Active anomaly</span>
          <span className="mt-0.5 font-mono font-bold text-[#c9d1d9]">{node.active_anomaly_id ? "Yes" : "None reported"}</span>
        </div>
        <div className="flex flex-col rounded border border-[#21262d] bg-[#161b22] px-2 py-1">
          <span className="text-[8px] font-bold uppercase text-[#7d8590]">Throughput</span>
          <span className="mt-0.5 font-mono font-bold text-[#388bfd]">{metric(metrics.throughput_rps, "/s")}</span>
        </div>
      </div>
    </div>
  );
}

export function ServiceHealthCards() {
  const { connectionState } = useLiveLogs();
  const { topology, dataState } = useTopology();
  const workerState = useWorkerHealth();
  const status = displayOperationalStatus(dataState, connectionState);
  const nodes = topology?.nodes ?? [];
  const summary = useMemo(() => ({
    healthy: nodes.filter((node) => node.status === "healthy").length,
    degraded: nodes.filter((node) => node.status === "degraded").length,
    critical: nodes.filter((node) => node.status === "critical").length,
  }), [nodes]);

  return (
    <div className="rounded-xl border border-[#21262d] bg-[#161b22] p-4 shadow-sm">
      <div className="mb-3.5 flex items-center justify-between border-b border-[#21262d] pb-2.5">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-[#388bfd]" />
          <h3 className="text-sm font-bold text-[#e6edf3]">System Health &amp; Service Matrix</h3>
          <span className="rounded-full bg-[#388bfd]/10 px-2 py-0.5 text-[10px] font-bold text-[#388bfd]">
            {status === "available" || status === "stale" ? `${nodes.length} Reported` : status === "empty" ? "0 Reported" : "Unavailable"}
          </span>
        </div>
        {nodes.length > 0 ? (
          <div className="flex items-center gap-3 text-[11px]">
            <span className="text-[#3fb950]">{summary.healthy} Healthy</span>
            <span className="text-[#d29922]">{summary.degraded} Degraded</span>
            <span className="text-[#f85149]">{summary.critical} Critical</span>
          </div>
        ) : null}
      </div>

      <OperationalStateNotice
        state={dataState}
        connectionState={connectionState}
        emptyMessage="No service topology was returned for this scope."
        errorMessage="Authoritative service health is unavailable."
        className={nodes.length > 0 ? "mb-3" : ""}
      />

      {nodes.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {nodes.map((node) => <ServiceCard key={node.id} node={node} />)}
        </div>
      ) : null}

      <div className="mt-4 border-t border-[#21262d] pt-3">
        <div className="mb-2 flex items-center justify-between">
          <h4 className="text-xs font-bold text-[#e6edf3]">Worker heartbeats</h4>
          <span className="text-[10px] text-[#7d8590]">Durable runtime evidence</span>
        </div>
        <OperationalStateNotice
          state={workerState}
          emptyMessage="No worker heartbeat snapshot was returned."
          errorMessage="Worker heartbeat state is unavailable."
          className="mb-2"
        />
        {workerState.data && (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            {(["pipeline", "webhook", "archive"] as WorkerRole[]).map((role) => {
              const worker = workerState.data.workers[role];
              const color = worker.status === "healthy" ? "#3fb950" : worker.status === "stale" ? "#d29922" : "#f85149";
              return (
                <div key={role} className="rounded-lg border border-[#21262d] bg-[#0d1117] px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[11px] font-semibold capitalize text-[#c9d1d9]">{role}</span>
                    <span className="text-[10px] font-bold uppercase" style={{ color }}>{worker.status}</span>
                  </div>
                  <div className="mt-1 text-[10px] text-[#7d8590]">
                    {worker.age_seconds === null ? "No heartbeat observed" : `Heartbeat age ${worker.age_seconds.toFixed(1)}s`}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

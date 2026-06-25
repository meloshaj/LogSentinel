import { mockRootCauses, serviceGraph } from "../services/mockMonitoringData";
import { DependencyGraph } from "../components/common/DependencyGraph";
import { Brain, ChevronRight, Lightbulb, Network, Sparkles, Target, Wrench } from "lucide-react";

const AI_SUMMARIES = [
  {
    service: "database-service",
    summary: "The primary failure origin. Connection pool exhausted at 200/200 active connections, coinciding with OOM kill on replica node db-replica-03. This caused cascading timeouts across all dependent services. The root trigger appears to be a long-running transaction initiated at 14:28 that held locks on the transactions table, preventing pool recycling.",
    severity: "#f85149",
  },
  {
    service: "payment-service",
    summary: "Secondary failure due to upstream DB dependency. After 3 failed retries (each timing out at 5000ms), the circuit breaker opened at 14:32:06. All payment flows are currently being shed. Stripe webhook queue is backing up - approximately 340 unprocessed events in the last 4 minutes.",
    severity: "#ffa657",
  },
  {
    service: "api-gateway",
    summary: "Collateral degradation. The gateway is correctly returning 503 to clients when upstream circuit breakers are open. Rate limit thresholds are approaching (78%) due to retry storms from clients. No misconfiguration detected - this service is behaving correctly under pressure.",
    severity: "#d29922",
  },
];

const FIXES = [
  { priority: 1, action: "Kill long-running transaction on database-service", detail: "Run: SELECT pg_cancel_backend(pid) for any query running > 60s on the transactions table. This will free pool connections immediately.", effort: "2 min" },
  { priority: 2, action: "Scale db-replica-03 replacement", detail: "Provision a new replica from the latest snapshot. ETA ~8 min. The primary is still healthy but under heavy load.", effort: "8 min" },
  { priority: 3, action: "Reset payment-service circuit breaker", detail: "Once DB connectivity is restored, manually trigger circuit breaker reset via POST /admin/circuit-breaker/reset to resume payment flows.", effort: "1 min" },
  { priority: 4, action: "Drain Stripe webhook backlog", detail: "Increase webhook consumer concurrency from 4 to 12 temporarily. Monitor for DB load increase.", effort: "5 min" },
];

export function AIAnalysisPage() {
  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-[#bc8cff]" />
            <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>AI Analysis</h1>
            <span className="px-2 py-0.5 rounded-full bg-[#bc8cff]/15 text-[#bc8cff] border border-[#bc8cff]/25" style={{ fontSize: "10px", fontWeight: 600 }}>
              BETA
            </span>
          </div>
          <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Root cause analysis, log summaries, and remediation suggestions powered by ML</p>
        </div>
        <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-[#bc8cff]/10 border border-[#bc8cff]/20">
          <Sparkles className="w-3.5 h-3.5 text-[#bc8cff]" />
          <span className="text-[#bc8cff]" style={{ fontSize: "11px", fontWeight: 500 }}>Analyzing 2.45M logs</span>
        </div>
      </div>

      {/* Root Cause Ranking */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <Target className="w-4 h-4 text-[#f85149]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Root Cause Ranking</span>
          <span className="ml-auto text-[#484f58]" style={{ fontSize: "10px" }}>ML-ranked - confidence based on log patterns</span>
        </div>
        <div className="p-4 space-y-2">
          {mockRootCauses.map((rc, idx) => {
            const pct = Math.round(rc.probability * 100);
            const color = pct >= 85 ? "#f85149" : pct >= 65 ? "#d29922" : "#7d8590";
            return (
              <div key={rc.id} className="flex items-start gap-4 p-3 rounded-lg bg-[#0d1117] border border-[#21262d]">
                <span className="flex items-center justify-center w-6 h-6 rounded shrink-0 mt-0.5 bg-[#21262d]" style={{ fontSize: "11px", fontWeight: 700, color }}>#{idx + 1}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>{rc.service}</span>
                    <span style={{ fontSize: "16px", fontWeight: 800, color }}>{pct}%</span>
                  </div>
                  <div className="w-full h-1.5 rounded-full bg-[#21262d] overflow-hidden mt-1.5 mb-1">
                    <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
                  </div>
                  <p className="text-[#7d8590]" style={{ fontSize: "11px" }}>{rc.issue}</p>
                  {rc.affectedDeps.length > 0 && (
                    <div className="flex gap-1.5 mt-1.5 flex-wrap">
                      <span className="text-[#484f58]" style={{ fontSize: "10px" }}>Cascades to:</span>
                      {rc.affectedDeps.map((d) => <span key={d} className="px-1.5 py-0.5 rounded bg-[#21262d] text-[#7d8590]" style={{ fontSize: "9px", fontFamily: "monospace" }}>{d}</span>)}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Two column: AI summaries + dependency graph */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* AI Log Summaries */}
        <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
            <Lightbulb className="w-4 h-4 text-[#d29922]" />
            <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>AI Log Summaries</span>
          </div>
          <div className="p-4 space-y-4">
            {AI_SUMMARIES.map((s) => (
              <div key={s.service} className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.severity }} />
                  <span className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 600 }}>{s.service}</span>
                </div>
                <p className="text-[#7d8590] pl-4" style={{ fontSize: "11px", lineHeight: 1.6 }}>{s.summary}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Dependency graph */}
        <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
            <Network className="w-4 h-4 text-[#388bfd]" />
            <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Service Dependency Graph</span>
          </div>
          <div className="bg-[#0d1117]" style={{ height: 280 }}>
            <DependencyGraph graph={serviceGraph} glowRadius={20} nodeRadius={14} strokeWidth={2} labelOffset={28} />
          </div>
          <div className="flex gap-4 px-4 py-2 border-t border-[#21262d]">
            {[{ color: "#f85149", label: "Critical path" }, { color: "#d29922", label: "Warning" }, { color: "#3fb950", label: "Healthy" }].map((i) => (
              <div key={i.label} className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full" style={{ background: i.color }} />
                <span className="text-[#484f58]" style={{ fontSize: "9px" }}>{i.label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Suggested Fixes */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <Wrench className="w-4 h-4 text-[#3fb950]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Suggested Fixes</span>
          <span className="ml-auto text-[#484f58]" style={{ fontSize: "10px" }}>Ordered by impact - estimated effort</span>
        </div>
        <div className="p-4 space-y-2">
          {FIXES.map((fix) => (
            <div key={fix.priority} className="flex items-start gap-4 p-3.5 rounded-lg bg-[#0d1117] border border-[#21262d] hover:border-[#3fb950]/30 transition-colors group">
              <span className="flex items-center justify-center w-6 h-6 rounded-full shrink-0 mt-0.5 bg-[#3fb950]/15 text-[#3fb950]" style={{ fontSize: "11px", fontWeight: 700 }}>{fix.priority}</span>
              <div className="flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 600 }}>{fix.action}</span>
                  <span className="shrink-0 px-2 py-0.5 rounded bg-[#21262d] text-[#7d8590]" style={{ fontSize: "10px" }}>~{fix.effort}</span>
                </div>
                <p className="text-[#7d8590] mt-1" style={{ fontSize: "11px", lineHeight: 1.5 }}>{fix.detail}</p>
              </div>
              <ChevronRight className="w-4 h-4 text-[#484f58] group-hover:text-[#3fb950] transition-colors shrink-0 mt-0.5" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

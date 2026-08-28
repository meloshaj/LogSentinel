import { ServiceTopologyGraph } from "../components/topology/ServiceTopologyGraph";
import { ServiceInvestigationDrawer } from "../components/investigation/ServiceInvestigationDrawer";
import { useState } from "react";
import { Brain, ChevronRight, Lightbulb, Network, Sparkles, Target, Wrench, Activity, ShieldCheck } from "lucide-react";
import { useTelemetryStream } from "../hooks/useTelemetryStream";
import { useLiveLogs } from "../hooks/useLiveLogs";
import { useTopology } from "../hooks/useTopology";
import type { RootCause } from "../types/monitoring";

export function AIAnalysisPage() {
  const { activeTrackingLoops } = useTelemetryStream();
  const { totalLogCount } = useLiveLogs();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const { nodes } = useTopology();

  const rootCauses: RootCause[] = [];
  const dynamicSummaries: any[] = [];
  const dynamicFixes: any[] = [];
  
  if (activeTrackingLoops.length > 0) {
    const loop = activeTrackingLoops[0];
    
    // Add suspected root cause
    if (loop.suspected_root_service) {
      const normScore = loop.anomaly_score > 1 ? loop.anomaly_score / 100 : loop.anomaly_score;
      const blastNodes = (loop.blast_radius && Array.isArray(loop.blast_radius)) ? loop.blast_radius : [];
      const affected = blastNodes.map(r => r.service_name).filter(n => n && n !== loop.suspected_root_service);
      
      rootCauses.push({
        id: loop.window_id,
        service: loop.suspected_root_service,
        probability: Math.min(1.0, Math.max(0.0, normScore)),
        issue: `Detected ${loop.severity} anomaly pattern in ${loop.suspected_root_service}`,
        affectedDeps: affected
      });
      
      dynamicFixes.push({
        priority: 1, 
        action: `Investigate root service: ${loop.suspected_root_service}`, 
        detail: `Review recent deployments, DB connection pool contention, and log anomalies for ${loop.suspected_root_service}. Check telemetry metrics for latency spikes.`, 
        effort: "10 min"
      });

      if (affected.length > 0) {
        dynamicFixes.push({
          priority: 2,
          action: `Verify downstream dependencies: ${affected.join(", ")}`,
          detail: `Inspect cascade propagation and circuit breaker status across downstream dependent services.`,
          effort: "15 min"
        });
      }
    }

    const blastNodes = (loop.blast_radius && Array.isArray(loop.blast_radius)) ? loop.blast_radius : [];

    blastNodes.forEach((node: any) => {
      const score = (node.impact_score > 1 ? node.impact_score : node.impact_score * 100) || Math.round(loop.anomaly_score * 100);
      dynamicSummaries.push({
        service: node.service_name,
        summary: `Topological impact analysis indicates classification '${node.impact_classification || "direct"}'. Impact score is ${score.toFixed(0)} with propagation pathways across dependent services.`,
        severity: score > 75 ? "#ef4444" : score > 45 ? "#ffa657" : "#f59e0b",
      });
    });

    if (dynamicSummaries.length === 0 && loop.suspected_root_service) {
      dynamicSummaries.push({
        service: loop.suspected_root_service,
        summary: `Isolation Forest flagged window ${loop.window_id.substring(0, 8)} as anomalous with score ${loop.anomaly_score.toFixed(2)}. Service telemetry indicates anomalous error distribution.`,
        severity: loop.severity === "critical" ? "#ef4444" : "#f59e0b",
      });
    }
  }

  const primaryLoop = activeTrackingLoops[0];
  const affectedServices = Array.isArray(primaryLoop?.blast_radius)
    ? primaryLoop.blast_radius.map((node) => node.service_name)
    : [];
  const failurePaths = Array.isArray(primaryLoop?.blast_radius)
    ? primaryLoop.blast_radius
      .map((node) => node.propagation_path)
      .filter((path) => path.length > 1)
    : [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-[#bc8cff]" />
            <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>AI Root Cause Analysis</h1>
            <span className="px-2 py-0.5 rounded-full bg-[#bc8cff]/15 text-[#bc8cff] border border-[#bc8cff]/25 text-[10px] font-bold tracking-wider">
              ML ENGINE
            </span>
          </div>
          <p className="text-[#7d8590] mt-0.5 text-xs">Unsupervised anomaly correlation, graph blast-radius ranking, and automated remediation</p>
        </div>

        {/* Dynamic Log Count Badge */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[#bc8cff]/10 border border-[#bc8cff]/25 shadow-sm">
          <span className="w-2 h-2 rounded-full bg-[#bc8cff] animate-pulse" />
          <span className="text-[#bc8cff] text-xs font-mono font-bold">
            {totalLogCount > 0 ? `${totalLogCount.toLocaleString()} logs analyzed` : "Live ingestion streaming"}
          </span>
        </div>
      </div>

      {/* Blast Radius Ribbon */}
      {rootCauses.length > 0 && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between p-4 rounded-xl bg-[#ef4444]/10 border border-[#ef4444]/30 shadow-[0_0_20px_rgba(239,68,68,0.1)] gap-4 backdrop-blur-sm">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-[#ef4444]/20 rounded-xl text-[#ef4444] border border-[#ef4444]/30">
               <Target className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-[#e6edf3] font-bold text-sm">Blast Radius Analysis Active</h3>
              <p className="text-[#ef4444] text-xs mt-0.5 font-medium">
                Primary root-cause candidate: <span className="font-mono font-bold underline">{rootCauses[0].service}</span>
              </p>
            </div>
          </div>
          <div className="flex gap-6 text-left sm:text-right">
            <div className="flex flex-col">
               <span className="text-[#8b949e] text-[10px] uppercase font-bold tracking-wider">Impact Depth</span>
               <span className="text-[#e6edf3] font-mono text-sm font-bold mt-0.5">
                 {rootCauses[0].affectedDeps.length > 0 ? rootCauses[0].affectedDeps.length + 1 : 1} Nodes
               </span>
            </div>
            <div className="flex flex-col">
               <span className="text-[#8b949e] text-[10px] uppercase font-bold tracking-wider">Cascades To</span>
               <span className="text-[#e6edf3] font-mono text-sm mt-0.5 truncate max-w-[180px]">
                 {rootCauses[0].affectedDeps.join(', ') || 'No cascading nodes'}
               </span>
            </div>
          </div>
        </div>
      )}

      {/* Root Cause Ranking */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <Target className="w-4 h-4 text-[#ef4444]" />
          <span className="text-[#e6edf3] text-[13px] font-bold">Root Cause Ranking</span>
          <span className="ml-auto text-[#7d8590] text-[10px]">Graph pathway algorithm & statistical ranking</span>
        </div>
        <div className="p-4 space-y-2.5">
          {rootCauses.length === 0 && (
            <div className="flex flex-col items-center justify-center py-8 text-center text-[#7d8590]">
              <ShieldCheck className="w-8 h-8 text-[#3fb950] mb-2" />
              <span className="text-sm font-semibold text-[#e6edf3]">No root causes detected</span>
              <span className="text-xs text-[#7d8590] mt-1">All service metrics and transaction pathways are behaving nominally.</span>
            </div>
          )}
          {rootCauses.map((rc, idx) => {
            const pct = Math.round(rc.probability * 100);
            return (
              <div key={rc.id} className="p-4 rounded-xl bg-[#0d1117] border border-[#21262d] flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="w-5 h-5 rounded-full bg-[#ef4444]/20 text-[#ef4444] flex items-center justify-center text-xs font-bold font-mono">
                      #{idx + 1}
                    </span>
                    <span className="text-[#e6edf3] font-bold font-mono text-sm">{rc.service}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[#8b949e] text-xs">Root Cause Probability:</span>
                    <span className="font-mono font-bold text-sm text-[#ef4444]">{pct}%</span>
                  </div>
                </div>
                <p className="text-[#8b949e] text-xs">{rc.issue}</p>
                {rc.affectedDeps.length > 0 && (
                  <div className="flex items-center gap-1.5 mt-1 text-[11px] text-[#7d8590]">
                    <span>Propagating failure to:</span>
                    <div className="flex gap-1 flex-wrap">
                      {rc.affectedDeps.map(dep => (
                        <span key={dep} className="px-1.5 py-0.5 rounded bg-[#161b22] text-[#f59e0b] font-mono text-[10px] border border-[#30363d]">
                          {dep}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm">
        <div className="relative h-[520px] w-full">
          <ServiceTopologyGraph
            mode="root-cause-focus"
            selectedNodeId={selectedNodeId}
            onNodeSelect={setSelectedNodeId}
          />
        </div>
      </div>
      
      <ServiceInvestigationDrawer
        nodeId={selectedNodeId}
        nodes={nodes}
        onClose={() => setSelectedNodeId(null)}
      />

      {/* AI summaries remain available as supporting evidence. */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm flex flex-col">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <Lightbulb className="w-4 h-4 text-[#f59e0b]" />
          <span className="text-[#e6edf3] text-[13px] font-bold">AI Log & Template Summaries</span>
        </div>
        <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
            {dynamicSummaries.map((sum: any, idx: number) => (
              <div key={idx} className="p-3.5 rounded-xl bg-[#0d1117] border border-[#21262d]">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[#e6edf3] text-xs font-bold font-mono">{sum.service}</span>
                  <span className="w-2.5 h-2.5 rounded-full" style={{ background: sum.severity }} />
                </div>
                <p className="text-[#8b949e] text-xs leading-relaxed">
                  {sum.summary}
                </p>
              </div>
            ))}
            {dynamicSummaries.length === 0 && (
              <div className="text-[#7d8590] p-6 text-center text-xs">
                No active anomalies to summarize. Ingest logs to generate real-time AI summaries.
              </div>
            )}
        </div>
      </div>

      {/* Suggested Fixes */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <Wrench className="w-4 h-4 text-[#3fb950]" />
          <span className="text-[#e6edf3] text-[13px] font-bold">Remediation & Suggested Fixes</span>
          <span className="ml-auto text-[#7d8590] text-[10px]">Ordered by blast-radius impact</span>
        </div>
        <div className="p-4">
          <div className="space-y-2.5">
            {dynamicFixes.map((fix: any, idx: number) => (
              <div key={idx} className="flex gap-3.5 p-3.5 rounded-xl bg-[#0d1117] border border-[#21262d] hover:border-[#30363d] transition-colors">
                <div className="flex flex-col items-center justify-center w-7 h-7 rounded-lg bg-[#388bfd]/15 text-[#388bfd] font-bold text-xs shrink-0 mt-0.5 border border-[#388bfd]/30">
                  {fix.priority}
                </div>
                <div className="flex-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[#e6edf3] font-bold text-xs">{fix.action}</span>
                    <span className="text-[#7d8590] text-[10px]">Est. effort: {fix.effort}</span>
                  </div>
                  <p className="text-[#8b949e] text-xs mt-1 leading-relaxed">{fix.detail}</p>
                </div>
              </div>
            ))}
            {dynamicFixes.length === 0 && (
              <div className="text-center py-6 text-[#7d8590] text-xs">
                No active remediation steps required. All telemetry healthy.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

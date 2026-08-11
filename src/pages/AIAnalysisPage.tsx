import { ServiceTopologyGraph } from "../components/topology/ServiceTopologyGraph";
import { TopologySyncProvider } from "../hooks/useTopologySync";
import { Brain, ChevronRight, Lightbulb, Network, Sparkles, Target, Wrench } from "lucide-react";
import { useTelemetryStream } from "../hooks/useTelemetryStream";
import type { RootCause } from "../types/monitoring";



export function AIAnalysisPage() {
  const { activeTrackingLoops } = useTelemetryStream();

  const rootCauses: RootCause[] = [];
  const dynamicSummaries: any[] = [];
  const dynamicFixes: any[] = [];
  
  if (activeTrackingLoops.length > 0) {
    const loop = activeTrackingLoops[0]; // just show first loop for analysis
    const nodesMap = new Map<string, any>();
    
    // Add suspected root
    if (loop.suspected_root_service) {
      nodesMap.set(loop.suspected_root_service, {
        id: loop.suspected_root_service,
        status: loop.severity === 'critical' ? 'Critical' : 'Warning'
      });
      const normScore = loop.anomaly_score > 1 ? loop.anomaly_score / 100 : loop.anomaly_score;
      rootCauses.push({
        id: loop.window_id,
        service: loop.suspected_root_service,
        probability: Math.min(1.0, Math.max(0.0, normScore)),
        issue: `Detected ${loop.severity} anomaly pattern in ${loop.suspected_root_service}`,
        affectedDeps: (loop.blast_radius || []).map(r => r.service_name).filter(n => n !== loop.suspected_root_service)
      });
      
      dynamicFixes.push({
        priority: 1, 
        action: `Investigate root service: ${loop.suspected_root_service}`, 
        detail: `Review recent deployments and log anomalies for ${loop.suspected_root_service}. Check infrastructure health metrics for resource exhaustion.`, 
        effort: "10 min"
      });
    }

    const blastNodes = loop.blast_radius || [];

    blastNodes.forEach((node: any) => {
      nodesMap.set(node.service_name, {
        id: node.service_name,
        status: node.impact_score > 80 ? 'Critical' : node.impact_score > 50 ? 'Warning' : 'Normal'
      });
      
      dynamicSummaries.push({
        service: node.service_name,
        summary: `Impact analysis indicates a classification of '${node.impact_classification}'. The node has an impact score of ${(node.impact_score > 1 ? node.impact_score : node.impact_score * 100).toFixed(0)} and affects propagation paths: [${node.propagation_path?.join(", ") || "none"}].`,
        severity: node.impact_score > 0.8 || node.impact_score > 80 ? "#f85149" : node.impact_score > 0.5 || node.impact_score > 50 ? "#ffa657" : "#d29922",
      });
    });
  }

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

      {/* Blast Radius Ribbon */}
      {rootCauses.length > 0 && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between p-4 rounded-xl bg-[#f85149]/10 border border-[#f85149]/20 shadow-[0_0_15px_rgba(248,81,73,0.1)] gap-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-[#f85149]/20 rounded-lg text-[#f85149]">
               <Target className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-[#e6edf3] font-semibold text-sm">Blast Radius Analysis Active</h3>
              <p className="text-[#f85149] text-xs mt-0.5">Primary vector isolated: <span className="font-mono">{rootCauses[0].service}</span></p>
            </div>
          </div>
          <div className="flex gap-6">
            <div className="flex flex-col">
               <span className="text-[#7d8590] text-[10px] uppercase font-bold tracking-wider">Impact Depth</span>
               <span className="text-[#e6edf3] font-mono text-sm mt-0.5">{rootCauses[0].affectedDeps.length > 0 ? rootCauses[0].affectedDeps.length + 1 : 1} Nodes</span>
            </div>
            <div className="flex flex-col">
               <span className="text-[#7d8590] text-[10px] uppercase font-bold tracking-wider">Affected Services</span>
               <span className="text-[#e6edf3] font-mono text-sm mt-0.5 truncate max-w-[150px]">{rootCauses[0].affectedDeps.join(', ') || 'None'}</span>
            </div>
          </div>
        </div>
      )}

      {/* Root Cause Ranking */}
      <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
          <Target className="w-4 h-4 text-[#f85149]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Root Cause Ranking</span>
          <span className="ml-auto text-[#484f58]" style={{ fontSize: "10px" }}>ML-ranked - confidence based on log patterns</span>
        </div>
        <div className="p-4 space-y-2">
          {rootCauses.length === 0 && (
            <div className="text-center py-8 text-[#7d8590]" style={{ fontSize: "12px" }}>
              No root causes detected at this time.
            </div>
          )}
          {rootCauses.map((rc, idx) => {
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
          <div className="space-y-3 p-4">
            {dynamicSummaries.map((sum: any, idx: number) => (
              <div key={idx} className="p-3 rounded-lg bg-[#0d1117] border border-[#21262d]">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[#c9d1d9]" style={{ fontSize: "13px", fontWeight: 600 }}>{sum.service}</span>
                  <span className="w-2 h-2 rounded-full" style={{ background: sum.severity }} />
                </div>
                <p className="text-[#7d8590]" style={{ fontSize: "12px", lineHeight: 1.5 }}>
                  {sum.summary}
                </p>
              </div>
            ))}
            {dynamicSummaries.length === 0 && (
              <div className="text-[#7d8590] p-4 text-center text-sm">No analysis available.</div>
            )}
          </div>
        </div>

        {/* Dependency graph */}
        <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
            <Network className="w-4 h-4 text-[#388bfd]" />
            <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Service Dependency Graph</span>
          </div>
          <div className="bg-[#0d1117] relative" style={{ height: 380 }}>
            <TopologySyncProvider>
              <ServiceTopologyGraph mode="root-cause-focus" />
            </TopologySyncProvider>
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
        <div className="p-4">
          <div className="space-y-2">
            {dynamicFixes.map((fix: any, idx: number) => (
              <div key={idx} className="flex gap-3 p-3 rounded-lg bg-[#0d1117] border border-[#21262d] hover:border-[#30363d] transition-colors">
                <div className="flex flex-col items-center justify-center w-6 h-6 rounded-full bg-[#388bfd]/10 text-[#388bfd] font-bold text-[10px] shrink-0 mt-0.5">
                  {fix.priority}
                </div>
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 600 }}>{fix.action}</span>
                    <span className="text-[#484f58]" style={{ fontSize: "10px" }}>{fix.effort}</span>
                  </div>
                  <p className="text-[#7d8590] mt-1" style={{ fontSize: "11px", lineHeight: 1.5 }}>{fix.detail}</p>
                </div>
              </div>
            ))}
            {dynamicFixes.length === 0 && (
              <div className="text-[#7d8590] p-4 text-center text-sm">No remediation steps available.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

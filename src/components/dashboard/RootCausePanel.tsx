import { Network, Target, Loader2 } from "lucide-react";
import { DependencyGraph } from "../common/DependencyGraph";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";

export function RootCausePanel() {
  const { activeTrackingLoops } = useTelemetryStream();

  // Extract root causes from active tracking loops
  const rootCauses = activeTrackingLoops.flatMap(loop => {
    if (!loop.blast_radius?.blast_radius) return [];
    
    return loop.blast_radius.blast_radius
      .filter(node => node.impact_classification === "root")
      .map(node => {
        // Find direct dependencies affected by this root
        const affectedDeps = loop.blast_radius!.blast_radius
          .filter(n => n.impact_classification === "direct" && n.dependency_path.includes(node.service_name))
          .map(n => n.service_name);
          
        return {
          id: `${loop.window_id}-${node.service_name}`,
          service: node.service_name,
          probability: Math.min(1.0, loop.anomaly_score / 100), // Approximate probability from anomaly score
          issue: `Detected anomaly loop (Severity: ${loop.severity}) with impact score ${node.impact_score}`,
          affectedDeps,
        };
      });
  }).sort((a, b) => b.probability - a.probability).slice(0, 5); // Take top 5

  // Generate dynamic topology graph from telemetry
  const dynamicGraph = (() => {
    if (activeTrackingLoops.length === 0) return { nodes: [], edges: [] };
    
    const nodes: Array<{ id: string; x: number; y: number }> = [];
    const edges: Array<{ from: string; to: string }> = [];
    const processedServices = new Set<string>();

    activeTrackingLoops.forEach(loop => {
      if (!loop.blast_radius?.blast_radius) return;
      
      loop.blast_radius.blast_radius.forEach((node, idx) => {
        if (!processedServices.has(node.service_name)) {
          processedServices.add(node.service_name);
          const isRoot = node.impact_classification === 'root';
          // basic deterministic layout
          nodes.push({
            id: node.service_name,
            x: 250 + (idx % 2 === 0 ? idx * 40 : -idx * 40),
            y: isRoot ? 20 : 100 + (idx * 40)
          });
        }
        
        if (node.dependency_path) {
          node.dependency_path.forEach(dep => {
            edges.push({ from: dep, to: node.service_name });
          });
        }
      });
    });

    if (nodes.length === 0) return { nodes: [], edges: [] };
    return { nodes, edges };
  })();

  return (
    <div className="flex flex-col rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d] shrink-0">
        <Target className="w-4 h-4 text-[#f85149]" />
        <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Root Cause Analysis</span>
        <span className="ml-auto text-[#7d8590]" style={{ fontSize: "10px" }}>Live ML Tracking</span>
      </div>

      <div className="px-3 pt-3 space-y-1.5 min-h-[100px]">
        {rootCauses.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full py-4 text-[#7d8590]">
            <Loader2 className="w-5 h-5 mb-2 animate-spin text-[#3fb950]" />
            <span style={{ fontSize: "11px" }}>Monitoring telemetry streams...</span>
          </div>
        ) : rootCauses.map((rootCause, index) => {
          const percentage = Math.round(rootCause.probability * 100);
          const color = percentage >= 85 ? "#f85149" : percentage >= 65 ? "#d29922" : "#7d8590";
          const bg = percentage >= 85
            ? "bg-[#da3633]/10 border-[#da3633]/30"
            : percentage >= 65
              ? "bg-[#d29922]/10 border-[#d29922]/20"
              : "bg-[#21262d]/60 border-[#21262d]";

          return (
            <div key={rootCause.id} className={`flex items-start gap-3 p-2.5 rounded-lg border ${bg}`}>
              <div
                className="flex items-center justify-center w-5 h-5 rounded shrink-0 mt-0.5"
                style={{ background: "rgba(33,38,45,0.8)", fontSize: "10px", fontWeight: 700, color }}
              >
                #{index + 1}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 600 }}>{rootCause.service}</span>
                  <span style={{ fontSize: "13px", fontWeight: 700, color }}>{percentage}%</span>
                </div>
                <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "10px", lineHeight: 1.4 }}>{rootCause.issue}</p>
                {rootCause.affectedDeps.length > 0 && (
                  <div className="flex items-center gap-1 mt-1.5 flex-wrap">
                    <span className="text-[#484f58]" style={{ fontSize: "9px" }}>Affects:</span>
                    {rootCause.affectedDeps.map((dependency) => (
                      <span
                        key={dependency}
                        className="px-1 py-0.5 rounded bg-[#21262d] text-[#7d8590]"
                        style={{ fontSize: "9px", fontFamily: "monospace" }}
                      >
                        {dependency}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0">
                <div className="w-16 h-1.5 rounded-full bg-[#21262d] overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${percentage}%`, background: color }} />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="px-3 pt-2 pb-3">
        <div className="flex items-center gap-1.5 mb-2">
          <Network className="w-3 h-3 text-[#7d8590]" />
          <span className="text-[#484f58]" style={{ fontSize: "10px" }}>Service Dependency Graph</span>
        </div>
        <div className="rounded-lg bg-[#0d1117] border border-[#21262d] overflow-hidden" style={{ height: 180 }}>
          <DependencyGraph graph={dynamicGraph} />
        </div>
        <div className="flex gap-4 mt-2">
          {[
            { color: "#f85149", label: "Critical path" },
            { color: "#d29922", label: "Warning" },
            { color: "#3fb950", label: "Healthy" },
          ].map((item) => (
            <div key={item.label} className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full" style={{ background: item.color }} />
              <span className="text-[#484f58]" style={{ fontSize: "9px" }}>{item.label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

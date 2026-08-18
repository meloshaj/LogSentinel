import React, { useEffect, useState } from "react";
import { X, Activity, Server, Database, AlignLeft, ShieldAlert } from "lucide-react";
import { TopologyNode } from "../../types/topology";
import { useTelemetryStream } from "../../hooks/useTelemetryStream";
import { useLiveLogs } from "../../hooks/useLiveLogs";

export interface ServiceInvestigationDrawerProps {
  nodeId: string | null;
  nodes: TopologyNode[];
  onClose: () => void;
}

export function ServiceInvestigationDrawer({ nodeId, nodes, onClose }: ServiceInvestigationDrawerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const { activeTrackingLoops } = useTelemetryStream();
  const { filteredLogs } = useLiveLogs();

  useEffect(() => {
    if (nodeId) {
      setIsOpen(true);
    } else {
      setIsOpen(false);
    }
  }, [nodeId]);

  if (!nodeId) return null;

  const node = nodes.find(n => n.id === nodeId);
  if (!node) return null;

  // Filter logs for this service
  const serviceLogs = filteredLogs.filter((log: any) => {
    const serviceName = (log.service_name || log.service || "").toLowerCase();
    return serviceName === node.name.toLowerCase() || serviceName === node.id.toLowerCase();
  }).slice(-20);

  // Find active anomalies affecting this service
  const anomalies = activeTrackingLoops.filter(loop => {
    if (loop.suspected_root_service === node.name || loop.suspected_root_service === node.id) return true;
    const blastRadius = loop.blast_radius as any[];
    if (Array.isArray(blastRadius)) {
      return blastRadius.some(br => br.service_name === node.name || br.service_name === node.id);
    }
    return false;
  });

  return (
    <>
      {/* Backdrop */}
      <div 
        className={`fixed inset-0 bg-black/50 backdrop-blur-sm z-40 transition-opacity duration-300 ${isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
        onClick={onClose}
      />
      
      {/* Drawer */}
      <div 
        className={`fixed top-0 right-0 h-full w-full sm:w-[450px] bg-[#0d1117] border-l border-[#21262d] shadow-2xl z-50 transform transition-transform duration-300 ease-in-out flex flex-col ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-[#21262d] bg-[#161b22]">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${
              node.status === 'critical' ? 'bg-[#ef4444]/20 text-[#ef4444]' : 
              node.status === 'degraded' ? 'bg-[#f59e0b]/20 text-[#f59e0b]' : 
              'bg-[#388bfd]/20 text-[#388bfd]'
            }`}>
              {node.type === 'database' ? <Database className="w-5 h-5" /> : <Server className="w-5 h-5" />}
            </div>
            <div>
              <h2 className="text-[#e6edf3] font-bold text-lg leading-tight">{node.name}</h2>
              <span className="text-[#8b949e] text-xs capitalize flex items-center gap-1">
                <span className={`w-2 h-2 rounded-full ${
                  node.status === 'critical' ? 'bg-[#ef4444]' : 
                  node.status === 'degraded' ? 'bg-[#f59e0b]' : 
                  'bg-[#3fb950]'
                }`} />
                {node.status} {node.type}
              </span>
            </div>
          </div>
          <button 
            onClick={onClose}
            className="p-1.5 rounded-md text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6 scrollbar-thin scrollbar-thumb-[#30363d] scrollbar-track-transparent">
          
          {/* Metrics Overview */}
          <div className="space-y-3">
            <h3 className="text-[#e6edf3] text-sm font-semibold flex items-center gap-2">
              <Activity className="w-4 h-4 text-[#388bfd]" />
              Real-time Metrics
            </h3>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-[#161b22] border border-[#21262d] rounded-lg p-3">
                <div className="text-[#8b949e] text-[10px] uppercase font-semibold tracking-wider mb-1">Latency p95</div>
                <div className="text-[#e6edf3] font-mono text-lg">{node.metrics?.latency_p95_ms?.toFixed(1) || '0.0'}ms</div>
              </div>
              <div className="bg-[#161b22] border border-[#21262d] rounded-lg p-3">
                <div className="text-[#8b949e] text-[10px] uppercase font-semibold tracking-wider mb-1">Error Rate</div>
                <div className={`font-mono text-lg ${node.metrics?.error_rate_pct > 5 ? 'text-[#ef4444]' : 'text-[#e6edf3]'}`}>
                  {node.metrics?.error_rate_pct?.toFixed(2) || '0.00'}%
                </div>
              </div>
              <div className="bg-[#161b22] border border-[#21262d] rounded-lg p-3">
                <div className="text-[#8b949e] text-[10px] uppercase font-semibold tracking-wider mb-1">Throughput</div>
                <div className="text-[#e6edf3] font-mono text-lg">{node.metrics?.throughput_rps?.toFixed(0) || '0'} rps</div>
              </div>
            </div>
          </div>

          {/* Active Anomalies */}
          {anomalies.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-[#e6edf3] text-sm font-semibold flex items-center gap-2">
                <ShieldAlert className="w-4 h-4 text-[#ef4444]" />
                Active Tracking Loops
              </h3>
              <div className="space-y-2">
                {anomalies.map((anomaly, idx) => {
                  const isRoot = anomaly.suspected_root_service === node.name || anomaly.suspected_root_service === node.id;
                  return (
                    <div key={idx} className={`p-3 rounded-lg border ${isRoot ? 'bg-[#ef4444]/10 border-[#ef4444]/30' : 'bg-[#f59e0b]/10 border-[#f59e0b]/30'}`}>
                      <div className="flex justify-between items-center mb-2">
                        <span className={`text-xs font-bold px-2 py-0.5 rounded ${isRoot ? 'bg-[#ef4444] text-white' : 'bg-[#f59e0b] text-[#161b22]'}`}>
                          {isRoot ? 'ROOT CAUSE' : 'BLAST RADIUS'}
                        </span>
                        <span className="text-[#8b949e] text-xs font-mono">ID: {anomaly.window_id?.split('-')[0] || (anomaly as any).id || 'unknown'}</span>
                      </div>
                      <div className="text-sm text-[#e6edf3]">
                        Confidence: <span className="font-mono text-[#388bfd]">{(((anomaly as any).root_cause_confidence || 0) * 100).toFixed(1)}%</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Live Logs */}
          <div className="space-y-3">
            <h3 className="text-[#e6edf3] text-sm font-semibold flex items-center gap-2">
              <AlignLeft className="w-4 h-4 text-[#388bfd]" />
              Live Logs
            </h3>
            <div className="bg-[#161b22] border border-[#21262d] rounded-lg p-2 max-h-[300px] overflow-y-auto scrollbar-thin scrollbar-thumb-[#30363d] scrollbar-track-transparent">
              {serviceLogs.length > 0 ? (
                <div className="space-y-1">
                  {serviceLogs.map((log, idx) => (
                    <div key={idx} className="text-xs font-mono py-1 border-b border-[#21262d]/50 last:border-0 hover:bg-[#21262d]/30 px-1 rounded">
                      <div className="flex gap-2">
                        <span className="text-[#8b949e] whitespace-nowrap">
                          {new Date(log.timestamp).toLocaleTimeString([], { hour12: false, fractionalSecondDigits: 3 } as any)}
                        </span>
                        <span className={`${
                          log.level === 'ERROR' || log.level === 'FATAL' || log.level === 'CRITICAL' ? 'text-[#ef4444]' :
                          log.level === 'WARN' || (log.level as string) === 'warning' ? 'text-[#f59e0b]' :
                          'text-[#3fb950]'
                        } uppercase w-10`}>
                          {log.level.substring(0, 4)}
                        </span>
                        <span className="text-[#e6edf3] break-all">{log.message}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-6 text-[#8b949e] text-xs">
                  No recent logs available for this service.
                </div>
              )}
            </div>
          </div>

        </div>
      </div>
    </>
  );
}

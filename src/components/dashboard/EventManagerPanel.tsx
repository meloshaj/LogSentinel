import React, { useMemo } from 'react';
import { useTelemetryStream, TrackingLoopEvent, PerformanceEvent } from '../../hooks/useTelemetryStream';
import { useTopologySync } from '../../hooks/useTopologySync';
import { AlertCircle, Activity, Crosshair } from 'lucide-react';

const SEVERITY_COLORS: Record<string, string> = {
  critical: "text-red-400 bg-red-400/10 border-red-400/20",
  high: "text-orange-400 bg-orange-400/10 border-orange-400/20",
  medium: "text-yellow-400 bg-yellow-400/10 border-yellow-400/20",
  low: "text-gray-400 bg-gray-400/10 border-gray-400/20",
  normal: "text-green-400 bg-green-400/10 border-green-400/20"
};

export function EventManagerPanel() {
  const { activeTrackingLoops, latestPerformanceEvents } = useTelemetryStream();
  const { setSelectedNodeId } = useTopologySync();

  const unifiedEvents = useMemo(() => {
    const events: Array<{
      id: string;
      type: 'anomaly' | 'performance';
      severity: string;
      title: string;
      description: string;
      raw: TrackingLoopEvent | PerformanceEvent;
    }> = [];

    activeTrackingLoops.forEach((loop) => {
      events.push({
        id: loop.window_id,
        type: 'anomaly',
        severity: loop.severity || 'high',
        title: `Anomaly Detected`,
        description: loop.suspected_root_service 
          ? `Root cause suspected in ${loop.suspected_root_service}`
          : `Score: ${loop.anomaly_score.toFixed(2)}`,
        raw: loop,
      });
    });

    latestPerformanceEvents.forEach((perf) => {
      events.push({
        id: perf.metric_name,
        type: 'performance',
        severity: perf.severity || 'medium',
        title: `Performance Alert`,
        description: `${perf.metric_name}: ${Number(perf.current_value).toFixed(2)} (threshold: ${Number(perf.threshold).toFixed(2)})`,
        raw: perf,
      });
    });

    // Simple sort to bring critical/high to the top
    const severityWeight: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1, normal: 0 };
    return events.sort((a, b) => (severityWeight[b.severity] || 0) - (severityWeight[a.severity] || 0));
  }, [activeTrackingLoops, latestPerformanceEvents]);

  const handleEventClick = (event: any) => {
    if (event.type === 'anomaly') {
      const loop = event.raw as TrackingLoopEvent;
      if (loop.suspected_root_service) {
        setSelectedNodeId(loop.suspected_root_service);
      }
    }
  };

  return (
    <div className="flex flex-col h-full bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#21262d] rounded-xl overflow-hidden min-h-[400px] shadow-sm dark:shadow-none">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-200 dark:border-[#21262d]">
        <Activity className="w-4 h-4 text-[#388bfd]" />
        <span className="text-slate-900 dark:text-[#e6edf3] text-sm font-semibold">Event Manager</span>
        <span className="ml-auto px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-[#21262d] text-slate-700 dark:text-[#e6edf3] text-xs font-bold">
          {unifiedEvents.length}
        </span>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {unifiedEvents.length === 0 ? (
          <div className="text-center text-slate-500 dark:text-[#7d8590] text-sm mt-8">No active events</div>
        ) : (
          unifiedEvents.map((evt) => (
            <div
              key={evt.id}
              onClick={() => handleEventClick(evt)}
              className={`p-3 rounded-lg border cursor-pointer hover:brightness-[0.9] dark:hover:brightness-125 transition-all ${SEVERITY_COLORS[evt.severity] || SEVERITY_COLORS.normal}`}
            >
              <div className="flex items-center gap-2">
                {evt.type === 'anomaly' ? <Crosshair className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                <span className="font-bold text-[10px] uppercase tracking-wider">{evt.severity}</span>
              </div>
              <div className="mt-1 font-semibold text-sm text-slate-800 dark:text-white">{evt.title}</div>
              <div className="mt-1 text-xs text-slate-600 dark:text-gray-300 opacity-90">{evt.description}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

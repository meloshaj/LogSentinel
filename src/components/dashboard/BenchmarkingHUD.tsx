import React from 'react';
import { useTelemetryStream } from '../../hooks/useTelemetryStream';
import { useLiveLogs } from '../../hooks/useLiveLogs';
import { Zap, Database, Layers, Activity } from 'lucide-react';

function StatCard({ title, value, threshold, severity, icon: Icon, showThreshold = true }: any) {
  const isBreached = showThreshold && value > threshold;
  const color = isBreached ? (severity === 'critical' ? 'text-red-400' : 'text-orange-400') : 'text-green-400';
  
  return (
    <div className="flex flex-col gap-1 p-3 rounded-lg bg-[#0d1117] border border-[#21262d]">
      <div className="flex items-center gap-2 mb-1">
        <Icon className={`w-3.5 h-3.5 ${color}`} />
        <span className="text-[#8b949e] font-semibold text-[10px] uppercase tracking-wider">{title}</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className={`text-xl font-bold ${color}`}>
          {showThreshold ? Number(value).toFixed(2) : value.toLocaleString()}
        </span>
        {showThreshold && (
          <span className="text-gray-500 text-xs font-mono">thr: {Number(threshold).toFixed(0)}</span>
        )}
      </div>
    </div>
  );
}

export function BenchmarkingHUD() {
  const { latestPerformanceEvents } = useTelemetryStream();
  const { totalLogCount } = useLiveLogs();

  const getEvent = (name: string) => latestPerformanceEvents.find(e => e.metric_name.includes(name));

  // Fallback defaults if no event has arrived yet
  const throughput = getEvent('throughput') || { current_value: 0, threshold: 1000, severity: 'normal' };
  const dbBatch = getEvent('db_batch_duration') || getEvent('db_batch') || { current_value: 0, threshold: 50, severity: 'normal' };
  const queueDepth = getEvent('queue_depth') || getEvent('queue') || { current_value: 0, threshold: 500, severity: 'normal' };

  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
      <StatCard 
        title="Total Ingested" 
        value={totalLogCount} 
        threshold={0}
        severity="normal"
        icon={Activity}
        showThreshold={false}
      />
      <StatCard 
        title="Throughput (logs/sec)" 
        value={throughput.current_value} 
        threshold={throughput.threshold}
        severity={throughput.severity}
        icon={Zap}
      />
      <StatCard 
        title="Batch Insert Duration (ms)" 
        value={dbBatch.current_value} 
        threshold={dbBatch.threshold}
        severity={dbBatch.severity}
        icon={Database}
      />
      <StatCard 
        title="Memory Queue Depth" 
        value={queueDepth.current_value} 
        threshold={queueDepth.threshold}
        severity={queueDepth.severity}
        icon={Layers}
      />
    </div>
  );
}


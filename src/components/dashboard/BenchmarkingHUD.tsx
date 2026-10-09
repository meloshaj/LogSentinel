import React from 'react';
import { useTelemetryStream } from '../../hooks/useTelemetryStream';
import { useLiveLogs } from '../../hooks/useLiveLogs';
import { Zap, Database, Layers, Activity } from 'lucide-react';
import { displayOperationalStatus, operationalStatusLabel } from '../common/OperationalState';
import type { PerformanceEvent } from '../../providers/TelemetryProvider';

interface StatCardProps {
  title: string;
  value: string | number;
  threshold: number | null;
  severity?: string;
  icon: React.ElementType;
  showThreshold?: boolean;
}

function StatCard({ title, value, threshold, severity, icon: Icon, showThreshold = true }: StatCardProps) {
  const numericValue = typeof value === 'number' && Number.isFinite(value) ? value : null;
  const isAvailable = numericValue !== null && threshold !== null;
  const isBreached = showThreshold && isAvailable && numericValue > threshold;
  const color = !isAvailable
    ? 'text-slate-500'
    : isBreached
      ? (severity === 'critical' ? 'text-red-400' : 'text-orange-400')
      : 'text-green-400';
  
  return (
    <div className="flex flex-col gap-1 p-3 rounded-lg bg-[#0d1117] border border-[#21262d]">
      <div className="flex items-center gap-2 mb-1">
        <Icon className={`w-3.5 h-3.5 ${color}`} />
        <span className="text-[#8b949e] font-semibold text-[10px] uppercase tracking-wider">{title}</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className={`text-xl font-bold ${color}`}>
          {isAvailable && showThreshold ? numericValue.toFixed(2) : typeof value === 'number' ? value.toLocaleString() : value}
        </span>
        {showThreshold && threshold !== null && (
          <span className="text-gray-500 text-xs font-mono">thr: {threshold.toFixed(0)}</span>
        )}
      </div>
    </div>
  );
}

export function BenchmarkingHUD() {
  const { latestPerformanceEvents, connectionStatus } = useTelemetryStream();
  const { totalLogCount, dataState: logDataState } = useLiveLogs();
  const logStatus = displayOperationalStatus(logDataState, connectionStatus);
  const logsKnown = logStatus === 'available' || logStatus === 'empty' || logStatus === 'stale';

  const getEvent = (name: string) => latestPerformanceEvents.find(e => e.metric_name.includes(name));

  const throughput: PerformanceEvent | undefined = getEvent('throughput');
  const dbBatch: PerformanceEvent | undefined = getEvent('db_batch_duration') || getEvent('db_batch');
  const queueDepth: PerformanceEvent | undefined = getEvent('queue_depth') || getEvent('queue');

  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
      <StatCard 
        title="Total Ingested" 
        value={logsKnown ? totalLogCount : operationalStatusLabel(logStatus)}
        threshold={0}
        icon={Activity}
        showThreshold={false}
      />
      <StatCard 
        title="Throughput (logs/sec)" 
        value={throughput?.current_value ?? "Unavailable"}
        threshold={throughput?.threshold ?? null}
        severity={throughput?.severity}
        icon={Zap}
      />
      <StatCard 
        title="Batch Insert Duration (ms)" 
        value={dbBatch?.current_value ?? "Unavailable"}
        threshold={dbBatch?.threshold ?? null}
        severity={dbBatch?.severity}
        icon={Database}
      />
      <StatCard 
        title="Memory Queue Depth" 
        value={queueDepth?.current_value ?? "Unavailable"}
        threshold={queueDepth?.threshold ?? null}
        severity={queueDepth?.severity}
        icon={Layers}
      />
    </div>
  );
}

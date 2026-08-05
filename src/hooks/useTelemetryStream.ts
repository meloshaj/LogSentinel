import { useContext } from 'react';
import { TelemetryContext } from '../providers/TelemetryProvider';
export type { BlastRadiusNode, TrackingLoopEvent, PerformanceEvent } from '../types/telemetryEvents';

export function useTelemetryStream() {
  const context = useContext(TelemetryContext);
  if (!context) {
    throw new Error('useTelemetryStream must be used within a TelemetryProvider');
  }
  return context;
}

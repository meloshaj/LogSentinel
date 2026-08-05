export type BlastRadiusNode = {
  service_name: string;
  impact_classification: 'root' | 'direct' | 'indirect';
  dependency_path: string[];
  propagation_path: string[];
  impact_score: number;
};

export type TrackingLoopEvent = {
  window_id: string;
  anomaly_score: number;
  severity: string;
  status: string;
  blast_radius?: BlastRadiusNode[] | null;
  suspected_root_service?: string | null;
};

export type PerformanceEvent = {
  metric_name: string;
  current_value: number;
  threshold: number;
  severity: string;
  health_metrics?: Record<string, unknown>;
};
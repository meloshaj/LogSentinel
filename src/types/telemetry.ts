export type TelemetryEventType =
  | "system.status"
  | "log.parsed"
  | "feature.window.closed"
  | "anomaly.detected";

export interface SystemStatusPayload {
  status: string;
  message: string;
}

export interface LogParsedPayload {
  source?: string | null;
  environment?: string | null;
  service: string;
  level: string;
  template_id: string;
  template?: string | null;
  correlation_id?: string | null;
}

export interface FeatureWindowClosedPayload {
  window_id: string;
  window_start?: string | null;
  window_end?: string | null;
  total_log_count: number;
  error_count: number;
  warning_count: number;
  error_ratio?: number | null;
  active_services?: number | null;
  unique_templates: number;
  burst_indicator?: number | null;
}

export interface AnomalyDetectedPayload {
  window_id: string;
  anomaly_score?: number | null;
  severity?: string | null;
  model_version?: string | null;
}

export type TelemetryPayload =
  | SystemStatusPayload
  | LogParsedPayload
  | FeatureWindowClosedPayload
  | AnomalyDetectedPayload
  | Record<string, unknown>;

export interface TelemetryEvent {
  type: TelemetryEventType;
  timestamp: string;
  payload: TelemetryPayload;
}

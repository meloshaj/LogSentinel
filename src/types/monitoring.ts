export type LogLevel = "INFO" | "WARN" | "ERROR" | "DEBUG";

export interface LogEntry {
  id: string;
  timestamp: string;
  level: LogLevel;
  service: string;
  message: string;
}

export interface ServiceAnomaly {
  id: string;
  name: string;
  score: number;
  status: "Normal" | "Warning" | "Critical";
  explanation: string;
  errorRate: number;
  latency: number;
}

export interface RootCause {
  id: string;
  service: string;
  probability: number;
  issue: string;
  affectedDeps: string[];
}

export interface Incident {
  id: string;
  service: string;
  severity: "low" | "medium" | "high" | "critical";
  timestamp: string;
  description: string;
  status: "open" | "investigating" | "resolved";
}

export interface MetricSeries {
  time: string;
  logs: number;
  errors: number;
  anomalies: number;
}

export interface ServiceGraph {
  nodes: Array<{ id: string; x: number; y: number }>;
  edges: Array<{ from: string; to: string }>;
}

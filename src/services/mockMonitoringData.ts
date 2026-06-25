import type { Incident, LogEntry, MetricSeries, RootCause, ServiceAnomaly, ServiceGraph } from "../types/monitoring";

// ------- Mock Data -------

export const mockLogs: LogEntry[] = [
  { id: "l1", timestamp: "14:32:01.124", level: "INFO", service: "api-gateway", message: "Request received: GET /api/v2/users" },
  { id: "l2", timestamp: "14:32:01.301", level: "INFO", service: "auth-service", message: "JWT token validated successfully" },
  { id: "l3", timestamp: "14:32:01.892", level: "WARN", service: "payment-service", message: "High latency detected: 843ms response time" },
  { id: "l4", timestamp: "14:32:02.015", level: "ERROR", service: "database-service", message: "Connection timeout after 5000ms - retrying (1/3)" },
  { id: "l5", timestamp: "14:32:02.310", level: "INFO", service: "cache-service", message: "Cache hit ratio: 87.4%" },
  { id: "l6", timestamp: "14:32:02.671", level: "ERROR", service: "database-service", message: "Connection timeout after 5000ms - retrying (2/3)" },
  { id: "l7", timestamp: "14:32:03.001", level: "WARN", service: "api-gateway", message: "Rate limit threshold at 78% - monitor closely" },
  { id: "l8", timestamp: "14:32:03.450", level: "DEBUG", service: "auth-service", message: "Session cache refreshed for 1,240 active sessions" },
  { id: "l9", timestamp: "14:32:03.812", level: "ERROR", service: "payment-service", message: "Upstream service unavailable: stripe-webhook returned 503" },
  { id: "l10", timestamp: "14:32:04.101", level: "INFO", service: "notification-svc", message: "Email dispatch queue processed - 234 emails sent" },
  { id: "l11", timestamp: "14:32:04.559", level: "WARN", service: "database-service", message: "Slow query detected: SELECT * FROM transactions took 2.4s" },
  { id: "l12", timestamp: "14:32:04.900", level: "INFO", service: "api-gateway", message: "Health check passed - all downstream services responding" },
  { id: "l13", timestamp: "14:32:05.213", level: "ERROR", service: "database-service", message: "Max connection pool reached - 200/200 connections active" },
  { id: "l14", timestamp: "14:32:05.678", level: "INFO", service: "cache-service", message: "Eviction policy triggered - 1,024 stale keys removed" },
  { id: "l15", timestamp: "14:32:06.001", level: "WARN", service: "payment-service", message: "Retry budget exhausted - circuit breaker opening" },
  { id: "l16", timestamp: "14:32:06.321", level: "INFO", service: "auth-service", message: "Service started successfully on port 8081" },
  { id: "l17", timestamp: "14:32:06.789", level: "ERROR", service: "payment-service", message: "Circuit breaker OPEN - requests being shed" },
  { id: "l18", timestamp: "14:32:07.100", level: "WARN", service: "api-gateway", message: "503 returned to client - upstream circuit open" },
  { id: "l19", timestamp: "14:32:07.450", level: "INFO", service: "notification-svc", message: "Alert fired: PagerDuty incident #INC-20445 created" },
  { id: "l20", timestamp: "14:32:07.801", level: "ERROR", service: "database-service", message: "OOM kill detected on replica node db-replica-03" },
];

export const mockAnomalies: ServiceAnomaly[] = [
  { id: "a1", name: "database-service", score: 0.94, status: "Critical", explanation: "Spike in error rate + connection pool exhaustion", errorRate: 38, latency: 2400 },
  { id: "a2", name: "payment-service", score: 0.81, status: "Critical", explanation: "Circuit breaker open - upstream dependency failing", errorRate: 27, latency: 1800 },
  { id: "a3", name: "api-gateway", score: 0.61, status: "Warning", explanation: "Elevated 5xx rate due to upstream failures", errorRate: 12, latency: 430 },
  { id: "a4", name: "auth-service", score: 0.38, status: "Warning", explanation: "Increased session cache miss rate observed", errorRate: 4, latency: 210 },
  { id: "a5", name: "notification-svc", score: 0.12, status: "Normal", explanation: "Operating within normal parameters", errorRate: 1, latency: 95 },
  { id: "a6", name: "cache-service", score: 0.08, status: "Normal", explanation: "Healthy - eviction within expected bounds", errorRate: 0, latency: 4 },
];

export const mockRootCauses: RootCause[] = [
  { id: "r1", service: "database-service", probability: 0.92, issue: "Connection pool exhaustion & OOM on replica", affectedDeps: ["payment-service", "api-gateway", "auth-service"] },
  { id: "r2", service: "auth-service", probability: 0.78, issue: "Session cache degradation increasing DB load", affectedDeps: ["api-gateway"] },
  { id: "r3", service: "payment-service", probability: 0.63, issue: "Stripe webhook latency triggering cascading timeouts", affectedDeps: ["api-gateway", "notification-svc"] },
  { id: "r4", service: "api-gateway", probability: 0.41, issue: "Rate limiter misconfiguration under high load", affectedDeps: [] },
];

export const mockIncidents: Incident[] = [
  { id: "i1", service: "database-service", severity: "critical", timestamp: "14:32:05", description: "Max connection pool reached - full DB saturation", status: "investigating" },
  { id: "i2", service: "payment-service", severity: "high", timestamp: "14:32:06", description: "Circuit breaker opened - payment flow degraded", status: "investigating" },
  { id: "i3", service: "api-gateway", severity: "medium", timestamp: "14:32:07", description: "503 errors returned to clients - upstream unavailable", status: "open" },
  { id: "i4", service: "auth-service", severity: "medium", timestamp: "14:28:12", description: "Elevated token validation latency - SLO at risk", status: "open" },
  { id: "i5", service: "notification-svc", severity: "low", timestamp: "14:15:00", description: "Email queue backlog growing - throughput reduced", status: "resolved" },
];

export const mockTimeSeriesData: MetricSeries[] = [
  { time: "14:00", logs: 1200, errors: 12, anomalies: 0 },
  { time: "14:05", logs: 1350, errors: 18, anomalies: 1 },
  { time: "14:10", logs: 1180, errors: 9, anomalies: 0 },
  { time: "14:15", logs: 1420, errors: 31, anomalies: 1 },
  { time: "14:20", logs: 1560, errors: 44, anomalies: 2 },
  { time: "14:25", logs: 1700, errors: 58, anomalies: 2 },
  { time: "14:30", logs: 2100, errors: 142, anomalies: 4 },
  { time: "14:32", logs: 2450, errors: 287, anomalies: 6 },
];

export const serviceGraph: ServiceGraph = {
  nodes: [
    { id: "api-gateway", x: 200, y: 80 },
    { id: "auth-service", x: 80, y: 200 },
    { id: "payment-service", x: 200, y: 200 },
    { id: "database-service", x: 140, y: 320 },
    { id: "cache-service", x: 280, y: 320 },
    { id: "notification-svc", x: 320, y: 200 },
  ],
  edges: [
    { from: "api-gateway", to: "auth-service" },
    { from: "api-gateway", to: "payment-service" },
    { from: "api-gateway", to: "notification-svc" },
    { from: "auth-service", to: "database-service" },
    { from: "auth-service", to: "cache-service" },
    { from: "payment-service", to: "database-service" },
    { from: "notification-svc", to: "database-service" },
  ],
};

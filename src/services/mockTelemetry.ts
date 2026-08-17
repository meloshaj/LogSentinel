/**
 * Mock Telemetry Emitter for Standalone / Demo Mode (VITE_DEMO_MODE=true).
 *
 * Emulates the `/ws/telemetry` WebSocket protocol and historical log backfill
 * completely in the browser without requiring live Docker containers or backend APIs.
 *
 * Generates typed events:
 * - `system.status` (worker status, memory, queue depth)
 * - `log.parsed` (synthetic microservice logs with masked templates)
 * - `feature.window.closed` (feature distributions)
 * - `anomaly.detected` & `infrastructure.tracking_loop.triggered` (root cause & blast radius)
 */

import type { LogEntry } from "../types/monitoring";
import type { TelemetryEvent } from "../types/telemetry";

export interface MockTelemetryOptions {
  logIntervalMs?: number;
  anomalyIntervalMs?: number;
  statusIntervalMs?: number;
}

const SERVICES = [
  "api-gateway",
  "auth-service",
  "order-service",
  "payment-gateway",
  "postgres-db",
];

const TEMPLATES: Record<string, { tpl: string; level: string; msg: (params: any) => string }> = {
  gw_200: {
    tpl: "POST /api/v1/orders HTTP/1.1 <*:IP> status 200 user=<*:STR>",
    level: "INFO",
    msg: (p) => `POST /api/v1/orders HTTP/1.1 from ${p.ip} status 200 user=${p.user} duration=${p.dur}ms`,
  },
  auth_ok: {
    tpl: "Token validated for subject <*:STR> scope=orders.create",
    level: "INFO",
    msg: (p) => `Token validated for subject ${p.user} scope=orders.create issuer=logsentinel-auth`,
  },
  order_created: {
    tpl: "Order <*:ID> created for sku=<*:STR> quantity=1 amount=$<*:NUM>",
    level: "INFO",
    msg: (p) => `Order ${p.orderId} created for sku=${p.sku} quantity=1 amount=$${p.amount}`,
  },
  pay_auth: {
    tpl: "Payment captured tx_id=<*:STR> order_id=<*:ID> status=AUTHORIZED",
    level: "INFO",
    msg: (p) => `Payment captured tx_id=txn_${p.txId} order_id=${p.orderId} status=AUTHORIZED amount=$${p.amount}`,
  },
  db_commit: {
    tpl: "COMMIT order transaction <*:ID>; duration=<*:NUM>ms pool_available=<*:NUM>/100",
    level: "INFO",
    msg: (p) => `COMMIT order transaction ${p.orderId}; duration=4.2ms locks_acquired=2 pool_available=${p.pool}/100`,
  },
  db_pool_fatal: {
    tpl: "FATAL: connection pool exhausted (active=100/100, queued=<*:NUM>)",
    level: "ERROR",
    msg: (p) => `FATAL: connection pool exhausted (active=100/100, queued=${p.queued}). Transaction aborted.`,
  },
  pay_timeout: {
    tpl: "TimeoutError: Failed to acquire database connection after <*:NUM>ms",
    level: "ERROR",
    msg: (p) => `TimeoutError: Failed to acquire database connection after 5000ms for order ${p.orderId}. Downstream postgres-db unresponsive.`,
  },
  gw_504: {
    tpl: "POST /api/v1/orders HTTP/1.1 from <*:IP> status 504 GATEWAY_TIMEOUT",
    level: "ERROR",
    msg: (p) => `POST /api/v1/orders HTTP/1.1 from ${p.ip} status 504 GATEWAY_TIMEOUT upstream=order-service duration=5120ms`,
  },
};

function generateULID(): string {
  const time = Date.now().toString(36).padStart(10, "0").toUpperCase();
  const rand = Math.random().toString(36).substring(2, 18).toUpperCase();
  return (time + rand).substring(0, 26);
}

export class MockTelemetryEmitter {
  private listeners: Set<(event: TelemetryEvent) => void> = new Set();
  private timers: ReturnType<typeof setInterval>[] = [];
  private isRunning: boolean = false;
  private logCount: number = 0;

  constructor(private options: MockTelemetryOptions = {}) {}

  public subscribe(callback: (event: TelemetryEvent) => void): () => void {
    this.listeners.add(callback);
    return () => {
      this.listeners.delete(callback);
    };
  }

  private dispatch(event: TelemetryEvent) {
    this.listeners.forEach((listener) => {
      try {
        listener(event);
      } catch (err) {
        console.error("Error in mock telemetry subscriber:", err);
      }
    });
  }

  public getInitialBackfillLogs(count: number = 40): LogEntry[] {
    const logs: LogEntry[] = [];
    const now = Date.now();

    for (let i = count - 1; i >= 0; i--) {
      const timestamp = new Date(now - i * 3000).toISOString();
      const service = SERVICES[i % SERVICES.length];
      const isErr = i % 12 === 0;
      const tplKey = isErr ? "db_pool_fatal" : (["gw_200", "auth_ok", "order_created", "pay_auth", "db_commit"][i % 5]);
      const template = TEMPLATES[tplKey];

      const log: LogEntry = {
        id: generateULID(),
        service,
        level: (isErr ? "ERROR" : template.level) as any,
        message: template.msg({
          ip: `192.168.1.${(i % 50) + 10}`,
          user: `usr_${1000 + i}`,
          dur: 15 + (i % 20),
          orderId: `ord-${50000 + i}`,
          sku: `sku-prod-${i % 8}`,
          amount: (25 + (i % 150)).toFixed(2),
          txId: `tx${i}`,
          pool: 85 - (i % 10),
          queued: 400 + i,
        }),
        timestamp,
        latency_ms: isErr ? 5120 : 12 + (i % 25),
        template_id: `tpl-${tplKey}`,
        template_text: template.tpl,
        parameters: [],
        cluster_size: 50 + i,
        source: "demo-mode",
        environment: "production",
      };
      logs.push(log);
    }
    return logs;
  }

  public start() {
    if (this.isRunning) return;
    this.isRunning = true;

    const logInterval = this.options.logIntervalMs || 450;
    const statusInterval = this.options.statusIntervalMs || 6000;
    const anomalyInterval = this.options.anomalyIntervalMs || 25000;

    // 1. Continuous synthetic parsed logs
    const logTimer = setInterval(() => {
      this.emitSyntheticLog();
    }, logInterval);
    this.timers.push(logTimer);

    // 2. Periodic system.status events
    const statusTimer = setInterval(() => {
      this.emitSystemStatus();
    }, statusInterval);
    this.timers.push(statusTimer);

    // 3. Periodic realistic anomaly detection loop
    const anomalyTimer = setInterval(() => {
      this.emitAnomalyEvent();
    }, anomalyInterval);
    this.timers.push(anomalyTimer);

    // Initial trigger for immediate dashboard population
    setTimeout(() => this.emitSystemStatus(), 100);
    setTimeout(() => this.emitAnomalyEvent(), 800);
  }

  public stop() {
    this.isRunning = false;
    this.timers.forEach((t) => clearInterval(t));
    this.timers = [];
  }

  private emitSyntheticLog() {
    this.logCount += 1;
    const isIncident = this.logCount % 18 === 0 || this.logCount % 19 === 0;
    const service = isIncident
      ? (this.logCount % 2 === 0 ? "postgres-db" : "payment-gateway")
      : SERVICES[Math.floor(Math.random() * SERVICES.length)];

    const tplKey = isIncident
      ? (service === "postgres-db" ? "db_pool_fatal" : "pay_timeout")
      : (["gw_200", "auth_ok", "order_created", "pay_auth", "db_commit"][Math.floor(Math.random() * 5)]);

    const template = TEMPLATES[tplKey];
    const traceId = `trace-${Math.random().toString(36).substring(2, 10)}`;

    const logEvent: TelemetryEvent = {
      type: "log.parsed",
      timestamp: new Date().toISOString(),
      payload: {
        id: generateULID(),
        service,
        level: isIncident ? "ERROR" : template.level,
        raw_message: template.msg({
          ip: `192.168.1.${Math.floor(Math.random() * 100)}`,
          user: `usr_${Math.floor(Math.random() * 9000)}`,
          dur: isIncident ? 5020 : Math.floor(Math.random() * 30) + 10,
          orderId: `ord-${Math.floor(Math.random() * 80000)}`,
          sku: `sku-${Math.floor(Math.random() * 20)}`,
          amount: (Math.random() * 200 + 10).toFixed(2),
          txId: Math.random().toString(36).substring(2, 8),
          pool: isIncident ? 0 : Math.floor(Math.random() * 20) + 75,
          queued: Math.floor(Math.random() * 200) + 300,
        }),
        timestamp: new Date().toISOString(),
        latency_ms: isIncident ? 5020 : Math.floor(Math.random() * 30) + 10,
        template_id: `tpl-${tplKey}`,
        template_text: template.tpl,
        parameters: [],
        cluster_size: this.logCount,
        source: "mock-stream",
        environment: "production",
        metadata: {
          trace_id: traceId,
          service,
        },
      },
    };

    this.dispatch(logEvent);
  }

  private emitSystemStatus() {
    const statusEvent: TelemetryEvent = {
      type: "system.status",
      timestamp: new Date().toISOString(),
      payload: {
        status: "healthy",
        uptime_seconds: 86400,
        workers: {
          drain_worker: "active",
          feature_worker: "active",
          event_manager: "active",
        },
        memory_usage_mb: 248.5,
        ingest_rate_per_sec: 7.8,
        redis_stream_depth: 4,
        active_services: SERVICES.length,
      },
    };
    this.dispatch(statusEvent);
  }

  private emitAnomalyEvent() {
    const anomalyScore = 0.94;
    const windowId = `win-${generateULID().substring(0, 12)}`;

    const anomalyEvent: TelemetryEvent = {
      type: "anomaly.detected",
      timestamp: new Date().toISOString(),
      payload: {
        window_id: windowId,
        anomaly_score: anomalyScore,
        severity: "critical",
        status: "investigating",
        suspected_root_service: "postgres-db",
        root_cause_score: 0.96,
        confidence: 0.94,
        blast_radius: [
          {
            service_name: "postgres-db",
            impact_classification: "root",
            dependency_path: ["postgres-db"],
            propagation_path: ["postgres-db", "payment-gateway", "order-service", "api-gateway"],
            impact_score: 96,
          },
          {
            service_name: "payment-gateway",
            impact_classification: "direct",
            dependency_path: ["postgres-db", "payment-gateway"],
            propagation_path: ["payment-gateway", "order-service"],
            impact_score: 84,
          },
          {
            service_name: "order-service",
            impact_classification: "direct",
            dependency_path: ["postgres-db", "payment-gateway", "order-service"],
            propagation_path: ["order-service", "api-gateway"],
            impact_score: 72,
          },
          {
            service_name: "api-gateway",
            impact_classification: "indirect",
            dependency_path: ["postgres-db", "payment-gateway", "order-service", "api-gateway"],
            propagation_path: ["api-gateway"],
            impact_score: 58,
          },
        ],
      },
    };

    this.dispatch(anomalyEvent);
  }
}

export const mockTelemetry = new MockTelemetryEmitter();

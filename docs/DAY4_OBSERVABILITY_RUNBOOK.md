# Day 4: Observability, Dashboards & Alert Integration Runbook

This runbook outlines how to visualize LogSentinel's telemetry in Grafana and how to validate the deduplicated anomaly alerting pipeline via Webhooks.

---

## 📊 Step 1: Prometheus Metrics Verification

LogSentinel exposes a `/metrics` endpoint on the API pods using `prometheus-fastapi-instrumentator`. Ensure your Prometheus server is configured to scrape this endpoint.

### Custom Metrics Available:
1. `logsentinel_ingest_requests_total` (Counter): Total number of ingestion requests hitting `/v1/logs` or `/v1/ingest/bulk`.
2. `logsentinel_batch_ingestion_size_total` (Counter): Cumulative count of individual log records successfully appended to Valkey streams.
3. `logsentinel_active_websocket_connections` (Gauge): Current count of live clients connected to the telemetry and graph broadcast WebSocket.

**Validation Command:**
```bash
# Verify metrics are being exposed successfully on the API gateway
kubectl port-forward svc/logsentinel-api 8000:80
curl -s http://localhost:8000/metrics | grep logsentinel_
```

---

## 📈 Step 2: Grafana Dashboard Import & Setup

We recommend building a unified "LogSentinel Production Dashboard" in Grafana tracking the following critical health indicators:

### 1. Ingestion Throughput (Logs/Sec)
* **PromQL:** `rate(logsentinel_batch_ingestion_size_total[1m])`
* **Target:** Should roughly match your upstream forwarder (FluentBit/OTel) emit rate.

### 2. Valkey Queue Backlog
* **CLI Validation:** `XINFO GROUPS logs:stream`
* **Dashboard Goal:** Monitor the `pending` message count per consumer group. If this number grows linearly, worker scaling is required.

### 3. TimescaleDB CAGG Refresh Lag
* **SQL Validation:** Query the TimescaleDB job scheduler to ensure the `logs_rollup_1m` continuous aggregate policies are completing successfully.
```sql
SELECT job_id, total_runs, total_failures, last_successful_finish
FROM timescaledb_information.job_stats
WHERE hypertable_name = 'parsed_logs';
```

---

## 🚨 Step 3: Alert Webhook & Deduplication Validation

The `event-worker` is responsible for evaluating Graph pathways and dispatching critical anomaly alerts to Slack, Discord, or PagerDuty. To prevent alert fatigue during cascading failures, it utilizes a strict 15-minute sliding window deduplication algorithm backed by Valkey.

### Action Required: Setup Webhook Targets
In your Helm `values.yaml` or external Secrets provider, inject the webhook URL into the event worker's environment variables (e.g., `SLACK_WEBHOOK_URL` or `DISCORD_WEBHOOK_URL`).

### Validation Drill:
1. **Trigger a Spike:** Run the incident simulator script to fire dozens of identical anomaly logs.
   ```bash
   python scripts/simulate_incident.py --steady-duration 5 --incident-duration 15
   ```
2. **Observe the Deduplication Key:**
   ```bash
   # Check Valkey for the active cooldown key
   kubectl exec -it logsentinel-redis-0 -- redis-cli KEYS "alert_cooldown:*"
   # Expected output: alert_cooldown:orders-db:database_lock
   ```
3. **Verify Alert Channels:** Check your configured Slack/Discord channel. You should see **exactly one** high-severity alert for the incident, suppressing the remaining 49 duplicate triggers.
4. **Cooldown Expiry:** Ensure the Valkey key has a TTL (Time-To-Live) of 900 seconds (15 minutes).

---

## ✅ Day 4 Sign-Off Checklist
- [ ] Prometheus correctly scraping the `/metrics` endpoint across all API pods.
- [ ] Grafana Dashboard created highlighting `Ingestion Throughput` and `Active WebSockets`.
- [ ] Production webhook URL provisioned in external secrets for the `event-worker`.
- [ ] Alert deduplication cooldown (`alert_cooldown:*`) successfully validated in staging.

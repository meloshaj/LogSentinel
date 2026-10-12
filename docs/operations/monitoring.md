# Monitoring operations

The canonical metrics target is the internal API service. `/metrics` requires
the `METRICS_TOKEN` bearer credential and is intentionally absent from public
Caddy and Kubernetes ingress routes. Helm can render a `ServiceMonitor`; the
standalone Prometheus example reads the token from a mounted secret file.

## Pre-07B operating state

The application exposes the metrics endpoint and the repository contains a
Prometheus scrape/rules configuration. The OCI single-host deployment has
Docker healthchecks, PostgreSQL/Valkey health probes, and live pipeline,
webhook, and archive heartbeat keys. Before Remediation 07B, no Prometheus or
Alertmanager process was active on the OCI host, so metrics and rules were
configured source but not currently scraped or dispatched there. Do not infer
alerting from the existence of `/metrics` or `alerts.yml`; active state is
recorded in the 07B remediation evidence.

The API readiness probe means API process + PostgreSQL + Valkey. Standalone
worker readiness is independent:

| Role | Operational readiness contract |
| --- | --- |
| API | Process can serve; PostgreSQL and Valkey respond. |
| Pipeline | Process health, current Valkey heartbeat, startup DB/schema checks, Valkey consumer-group access. |
| Webhook/email | Process health, current heartbeat, startup DB/schema checks, durable outbox access, SMTP configuration contract. |
| Archive | Process health, current heartbeat, startup DB/schema checks, storage-client configuration contract. |

Worker Docker healthchecks currently prove the heartbeat path; DB/schema and
external checks are startup gates and should be rechecked by an authorized
operator when diagnosing a stale or unhealthy role. The API must not be made
dependent on every worker without an explicit architecture decision.

## Coverage classification (post-07B)

| Signal | State in production-like OCI deployment |
| --- | --- |
| API/internal metrics endpoint | IMPLEMENTED; internally verified, public edge blocked |
| Prometheus scrape and alert rule files | ACTIVE on OCI; 5/5 targets UP and 33 rules evaluated |
| Docker healthchecks | IMPLEMENTED and active for DB, Valkey, API, all workers, Caddy |
| Worker heartbeats | IMPLEMENTED and current for all three roles |
| Structured logs | IMPLEMENTED; bounded log review performed |
| Caddy, DB, Valkey health | IMPLEMENTED through local Docker probes |
| Disk/memory/restart/backup/WAL alert dispatch | ACTIVE through Prometheus, Alertmanager, and the approved operator relay |

## MetricsScrapeDown

Check API pod health, the internal metrics Service, token-secret mounting, and
Prometheus target errors. Do not expose `/metrics` publicly as a workaround.

## StreamConsumerLagHigh

Check the pipeline heartbeat, Valkey, consumer lag, and persistence failures.
The pipeline remains one replica while feature windows are process-local.

## StreamPendingEntriesHigh

Inspect pending ownership and idle time. Leave entries reclaimable; never
delete the stream or consumer group to clear this alert.

## PipelineDLQNotEmpty

Inspect redacted DLQ records and use an audited replay procedure after fixing
the underlying input/schema problem.

## WebhookOutboxBacklog

Check webhook heartbeat, database availability, destination provider status,
and network-policy egress.

## WebhookOldestPendingTooOld

Inspect outbox status, lease expiry, retry timestamps, and worker logs. Do not
manually mark an undelivered row as delivered.

## WebhookTerminalFailures

Review the safe failure category and tenant integration status, then use a
controlled requeue operation after correction.

## WorkerHeartbeatStale

Check the worker Deployment, process exit reason, Valkey, and recent rollout.
The metric is role-level and intentionally excludes pod instance labels.

## Backup/PITR failure visibility (06C.1)

The reviewed backup path exits nonzero for PostgreSQL preflight, dump,
checksum, manifest, remote upload, or remote object-verification failures.
When the host systemd unit is installed, inspect the unit result and bounded
journal output:

```bash
systemctl status --no-pager logsentinel-backup.service
journalctl --unit logsentinel-backup.service --since '48 hours ago' --no-pager
python3 /home/ubuntu/LogSentinel/scripts/backup_status.py
```

`backup_status.py` reports only checksum-valid local artifacts and does not
read or print credentials. PostgreSQL `pg_stat_archiver.failed_count` and the
last-failure fields provide the corresponding WAL failure signal. This was
failure detection and evidence visibility in the pre-07B baseline. After 07B,
the active Prometheus/Alertmanager path evaluates the corresponding metrics and
routes notifications through the approved operator receiver; current runtime
state and canary proof are recorded in the 07B evidence.

## Remediation 07B active monitoring

The production monitoring overlay is:

```text
backend / Docker / PostgreSQL / Valkey / recovery state
        -> Prometheus + node-exporter
        -> alert rules
        -> Alertmanager
        -> internal alert relay
        -> approved Discord operator webhook
```

Prometheus, Alertmanager, node-exporter, and the relay are internal-only
services on the monitoring network. They have no public Compose ports and must
not be published through Caddy. The host operations collector runs every 30
seconds from `logsentinel-ops-collector.timer`; it writes Prometheus textfile
metrics with temporary-file plus atomic-rename semantics. Its canary state is
the single character `0` or `1` in
`/var/lib/logsentinel-ops-collector/canary.state`.

The existing configured `DISCORD_WEBHOOK_URL` is reused as the approved
operator destination. Its value is supplied to the relay at runtime from the
production environment and is never committed to the repository, rendered into
the Alertmanager configuration, or logged. The relay accepts only HTTPS Discord
webhook hosts and sends a bounded message with mentions disabled. It does not
depend on LogSentinel workers or the application email path.

Metric retention is bounded to seven days and 512 MiB for the monitoring store.
This is monitoring-store protection and does not define application/data
retention policy.

### Operator workflow

Inspect monitoring state without public exposure:

```bash
cd /home/ubuntu/LogSentinel
docker compose --env-file .env -f deploy/monitoring/docker-compose.monitoring.yml ps
docker compose --env-file .env -f deploy/monitoring/docker-compose.monitoring.yml logs --since 15m prometheus alertmanager alert-relay
systemctl status --no-pager logsentinel-ops-collector.timer
```

For local UI/API inspection, use an SSH tunnel to loopback only after obtaining
operator authorization; do not bind monitoring ports to `0.0.0.0`. Prometheus
is at internal `:9090`, Alertmanager at internal `:9093`, and the relay health
endpoint is internal `:8080`.

The safe end-to-end canary is run only during an approved verification window:

```bash
printf '1\n' | sudo tee /var/lib/logsentinel-ops-collector/canary.state >/dev/null
systemctl start logsentinel-ops-collector.service
```

Wait for the canary alert to fire and the operator notification to arrive, then
clear it immediately:

```bash
printf '0\n' | sudo tee /var/lib/logsentinel-ops-collector/canary.state >/dev/null
systemctl start logsentinel-ops-collector.service
```

The 07B evidence records firing, delivery, clearing, and resolved delivery.
Never use a real application outage as an alert test. If notification delivery
fails, inspect Alertmanager routing and relay health; do not disable TLS or
print the webhook value.

### Alert threshold rationale

- Worker heartbeats use the implementation's 20-second update and 60-second
  TTL; the alert threshold is 90 seconds with a two-minute hold.
- Backup freshness warns at 22 hours and becomes critical at 24 hours, matching
  the approved RPO.
- WAL freshness uses 15 minutes: three five-minute `archive_timeout` intervals,
  allowing normal jitter while detecting a materially stale chain.
- Historical `pg_stat_archiver.failed_count` is a cumulative incident counter.
  Never reset it or require its absolute value to be zero. The alert evaluates
  `increase(...[15m])`; the operational health gate compares the counter at the
  start and end of the observation window and requires a zero delta, a fresh
  successful archive, no stale `.ready` backlog, and contiguous current remote
  WAL. A preserved historical failure count alone is not an active failure.
- Disk alerts use 85% warning and 90% critical; memory uses 85% warning and 95%
  critical, each with sustained durations to avoid transient noise.
- Queue and outbox alerts retain duration-based thresholds from the existing
  operational signals rather than alerting on harmless single-message activity.

## 07B runbook identifiers

The production rule annotations use these identifiers: `backendunavailable`,
`requiredcontainerdown`, `requiredcontainerunhealthy`, `containerrestart`,
`workerheartbeat`, `streambacklog`, `streampending`, `pipelinedlq`,
`streamobservation`, `outboxbacklog`, `outboxage`, `outboxfailures`,
`backuptimer`, `backupfailure`, `backupfreshness`, `pitrarchive`,
`diskpressure`, `memorypressure`, `monitoringhealth`, `collectorhealth`,
`notificationdelivery`, and `canary`.

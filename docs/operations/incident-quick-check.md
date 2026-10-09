# LogSentinel incident quick check

Start with read-only checks. Do not restart services, delete queues, purge
data, or restore a database until the incident evidence is preserved and an
operator authorizes the action.

```bash
cd /home/ubuntu/LogSentinel
date -u
docker compose --env-file .env -f docker-compose.prod.yml ps
docker inspect --format '{{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' $(docker ps -aq)
docker stats --no-stream
free -h
df -h /
uptime
```

## Site unavailable or Caddy failure

```bash
curl -k -i https://138.2.152.189.sslip.io/health
docker logs --since 15m logsentinel-caddy-1
docker exec logsentinel-caddy-1 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Check the host firewall, certificate/edge logs, and backend health before any
recreate. A Caddy-only change must not recreate the database or workers.

## API unhealthy

```bash
curl -k -i https://138.2.152.189.sslip.io/health
docker logs --since 15m logsentinel-backend-1
docker exec logsentinel-timescaledb-1 pg_isready -U logsentinel -d logsentinel_db
docker exec logsentinel_valkey_prod valkey-cli ping
```

If DB or Valkey is healthy, preserve backend diagnostics and inspect resource
pressure/restart history. Do not treat public `/ready` or `/readiness` 403 as
an API failure; those routes are intentionally blocked at Caddy.

## Pipeline stopped

```bash
docker inspect logsentinel-pipeline-worker-1 --format '{{json .State.Health}}'
docker logs --since 15m logsentinel-pipeline-worker-1
docker exec logsentinel_valkey_prod valkey-cli --scan --pattern 'logsentinel:worker-heartbeat:pipeline:*'
docker exec logsentinel_valkey_prod valkey-cli XINFO GROUPS 'STREAM_NAME'
```

Check the heartbeat TTL, pending entries, consumer lag, and database errors.
Do not delete a stream or consumer group to clear an alert.

## Email or webhooks stopped

```bash
docker inspect logsentinel-webhook-worker-1 --format '{{json .State.Health}}'
docker logs --since 15m logsentinel-webhook-worker-1
docker exec logsentinel-timescaledb-1 psql -U logsentinel -d logsentinel_db -c \
  "SELECT topic, status, count(*) FROM pipeline_outbox WHERE topic IN ('webhook','email') GROUP BY topic, status ORDER BY topic, status;"
```

Inspect safe error categories, leases, retry timestamps, and configuration
status. Do not send real probes or mark undelivered rows as delivered. Do not
print SMTP or integration secrets.

## Archive stopped

```bash
docker inspect logsentinel-archive-worker-1 --format '{{json .State.Health}}'
docker logs --since 15m logsentinel-archive-worker-1
docker exec logsentinel_valkey_prod valkey-cli --scan --pattern 'logsentinel:worker-heartbeat:archive:*'
```

Check storage-client configuration and database errors. Do not delete archive
manifests or objects as a diagnostic action.

## Database failure

```bash
docker inspect logsentinel-timescaledb-1 --format '{{json .State.Health}}'
docker exec logsentinel-timescaledb-1 pg_isready -U logsentinel -d logsentinel_db
docker logs --since 15m logsentinel-timescaledb-1
df -h /
```

Stop writes if integrity is uncertain, preserve evidence, verify a backup, and
follow the guarded restore sequence in `production-runbook.md`. Never restore
over production as a first diagnostic action.

## Valkey failure

```bash
docker inspect logsentinel_valkey_prod --format '{{json .State.Health}}'
docker exec logsentinel_valkey_prod valkey-cli ping
docker logs --since 15m logsentinel_valkey_prod
```

Expect API readiness and all worker heartbeats to be affected. Preserve stream
and consumer-group evidence before considering service recovery. Database
restore alone does not restore Valkey state.

## Disk or memory pressure

```bash
df -h
free -h
docker system df
docker stats --no-stream
du -xhd1 /home/ubuntu/LogSentinel /home/ubuntu/logsentinel-database-backups /home/ubuntu/logsentinel-deployment-backups 2>/dev/null
```

Do not prune images, containers, volumes, backups, logs, or database data until
the target and retention decision are explicitly reviewed.

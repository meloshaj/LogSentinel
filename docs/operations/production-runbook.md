# LogSentinel production runbook

This is the concise operator runbook for the single-host OCI deployment. It
contains no credentials. Cloudflare Pages is a separate deployment surface and
is not changed by OCI operations.

## Production topology

- Host: `ubuntu@138.2.152.189`
- Application directory: `/home/ubuntu/LogSentinel`
- Compose file: `/home/ubuntu/LogSentinel/docker-compose.prod.yml`
- Edge: Caddy; `/health` is public, `/ready`, `/readiness`, `/metrics`, and
  API documentation routes are denied at the edge.
- Services: PostgreSQL/TimescaleDB, Valkey, backend API, pipeline worker,
  webhook/email worker, archive worker, and Caddy.
- Migration owner: the one-shot `db-migrator` Compose service. The API and
  workers do not run migrations.

## Release authority

Production releases use the **MANUAL VERIFIED OCI RELEASE WORKFLOW**.
GitHub is the source repository, collaboration surface, and backup/version
history. It is not the production deployment authority, production credential
store, or a required production CI control plane. GitHub-hosted release-gate
enforcement and automatic GitHub-to-production deployment are not required or
supported.

The remote workflow remains older, the remote release gate is not enforced,
and the main ruleset does not require it. This is a repository-governance
observation and accepted architecture boundary. The local
`.github/workflows/ci.yml` remains useful; do not treat unpublished workflow
changes or remote rules as production controls.

Use [`retention-and-release.md`](retention-and-release.md) for the full local
gate, staging, OCI image archive, server verification, and evidence contract.
Eligibility is separate from deployment authorization. `release-ready` never
deploys. The operator invokes the controlled Compose deployment only after
the server-side predeploy verifier returns `PASS`.

## Connect and inspect

Use the operator-managed SSH key; never put its contents in a command, log, or
ticket.

```bash
ssh -i <operator-key> -o IdentitiesOnly=yes ubuntu@138.2.152.189
cd /home/ubuntu/LogSentinel
docker compose --env-file .env -f docker-compose.prod.yml ps
```

Read-only health checks:

```bash
curl -k -i https://138.2.152.189.sslip.io/health
curl -k -i https://138.2.152.189.sslip.io/ready       # expected: 403 at the edge
curl -k -i https://138.2.152.189.sslip.io/readiness  # expected: 403 at the edge
docker exec logsentinel-timescaledb-1 pg_isready -U logsentinel -d logsentinel_db
docker exec logsentinel_valkey_prod valkey-cli ping
docker inspect --format '{{.Name}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' \
  $(docker ps -q)
```

The API readiness contract is API process + PostgreSQL + Valkey. It is not an
end-to-end worker gate. Each standalone worker is observed separately through
its Docker healthcheck, Valkey heartbeat, startup DB/schema checks, logs, and
the worker metrics sampled by the API.

Heartbeat checks use TTL only; do not print values from `.env` or secret-bearing
payloads:

```bash
for role in pipeline webhook archive; do
  docker exec logsentinel_valkey_prod valkey-cli --raw --scan --pattern "logsentinel:worker-heartbeat:${role}:*" |
    while read key; do
      [ -n "$key" ] && docker exec logsentinel_valkey_prod valkey-cli TTL "$key"
    done
done
```

Logs are bounded and read-only:

```bash
docker logs --since 10m logsentinel-backend-1
docker logs --since 10m logsentinel-pipeline-worker-1
docker logs --since 10m logsentinel-webhook-worker-1
docker logs --since 10m logsentinel-archive-worker-1
docker logs --since 10m logsentinel-caddy-1
```

Internal `/metrics` requires the configured bearer token and must be queried
only from an authorized internal path. A public `/metrics` response of 403 is
expected.

## Backups

Run the reviewed backup profile from the approved backup execution
environment. It uses the same immutable backend image as production, whose
Dockerfile explicitly installs and asserts the PostgreSQL 16 client. The
script independently compares the `pg_dump` major version with the server
major version and fails closed on mismatch.

```bash
cd /home/ubuntu/LogSentinel
docker compose -f docker-compose.prod.yml --env-file .env --profile backup \
  run --rm backup
```

On the production host, Compose v5.5 resolves this profiled one-shot service
through `COMPOSE_PROFILES=backup`. The installed systemd service sets that
environment and uses `run --rm --no-deps backup`.

Keep the dump, `.sha256` sidecar, and manifest together. Verify the checksum
and use `pg_restore --list` before relying on an artifact. Do not expose dump
contents.

### Unattended backup scheduling and failure visibility

The OCI Compose file contains the reviewed backup profile, but it does not
contain a scheduler. The supported host-level scheduler is the repository-owned
systemd service and timer at `deploy/systemd/logsentinel-backup.service` and
`deploy/systemd/logsentinel-backup.timer`.

The approved production schedule is **daily at 02:00 UTC with up to a
15-minute randomized delay**. The timer is persistent, so systemd applies its
normal missed-run behavior after temporary host downtime. The service invokes
the exact Compose backup profile with `run --rm --no-deps backup`; it does not
start or recreate dependencies.

Validate the units without changing the host:

```bash
cd /home/ubuntu/LogSentinel
bash scripts/install_backup_scheduler.sh --check
```

Install it with the explicit approval gate. This runs the exact PG16 Compose
backup profile and does not start dependencies:

```bash
cd /home/ubuntu/LogSentinel
sudo env LOGSENTINEL_BACKUP_SCHEDULE_APPROVED=true \
  bash scripts/install_backup_scheduler.sh --install
systemctl list-timers --all logsentinel-backup.timer
systemctl status --no-pager logsentinel-backup.service
```

Do not claim active alert delivery from a failed unit. A failure is observable
through the unit exit status and bounded journal output. The last locally
retained checksum-valid backup can be inspected without printing credentials:

```bash
python3 scripts/backup_status.py
journalctl --unit logsentinel-backup.service --since '48 hours ago' --no-pager
```

The current OCI deployment has no verified Prometheus/Alertmanager dispatcher;
backup failure detection is implemented through the failed service result,
bounded journal, and `backup_status.py`, while active backup alert delivery
remains a separate monitoring remediation.

### Technical recovery capability and approved RPO/RTO

The current production configuration provides a PG16 logical dump plus SHA-256
sidecar, manifest, and remote object verification. PostgreSQL WAL archival is
active in production through the custom PG16/TimescaleDB image and the
repository-owned remote WAL helper. The logical dump does not capture Valkey
streams, consumer groups, pending entries, parser/cache state, or external
provider state.

06C.1 runtime verification recorded `archive_mode=on`, `wal_level=replica`,
`archive_timeout=5min`, and `fsync=on`/`synchronous_commit=on`. WAL objects use
the distinct `logsentinel/wal/` prefix, are uploaded over HTTPS with requested
server-side encryption, and are accepted only after remote size/checksum
verification. A production-generated physical base is recorded as
`20260916T125412Z-38ca363d`; its four-object manifest was retrieved and
verified in disposable PG16/TimescaleDB.

Activation recorded a cumulative PostgreSQL archiver `failed_count` of 45 from
the pre-final provider/name compatibility attempts. After the final helper
image and controlled database restart, the count remained stable and bounded
logs recorded no further archive failures; the current remote WAL inventory is
the authoritative recovery-path check.

The physical base-backup profile creates a PG16 tar/gzip base backup with
streamed backup WAL, uploads a manifest last, and verifies the complete object
set under `logsentinel/pitr-base/`. A logical dump is not a physical PITR base.

The operator-approved policy is:

- **Approved RPO: 24 hours.** This business maximum is not replaced by finer
  technical WAL granularity.
- **Approved RTO: 60 minutes.**

Technical recovery granularity is reported separately:

- logical recovery points are created by the daily 02:00 UTC scheduler plus
  manual/operator runs; the first exact scheduled-service invocation was
  verified at `2026-09-16T12:38:14Z`;
- healthy production WAL archival targets a maximum 300-second archive timeout,
  subject to PostgreSQL archiver and remote-storage success; and
- the latest valid logical point and latest verified WAL object are recorded in
  the current remediation evidence (`20260916T133925Z` and
  `00000001000000000000001D`, respectively).

06C measured a disposable rehearsal, but this value is not production recovery
time:

- measured remote download and PostgreSQL restore: recorded in the 06C DR JSON
  evidence;
- measured PITR clone recovery: recorded in the same evidence;
- measured combined application/worker startup and validation: recorded in the
  same evidence;
- measured disposable composite rehearsal: `27.571 seconds`;
- production destructive PITR drill: never performed;
- the 60-minute RTO includes real host, object-storage, provisioning, and
  operator variables that the disposable rehearsal does not include.

Retention/deletion policy remains a separate DATA-003 decision. Do not delete
remote recovery points as part of an incident response.

## Guarded restore procedure

Restore is an operator-authorized recovery action, never an automatic response
to an application bug. The sequence is:

1. Stop or drain writes and record the incident timeline, logs, container
   metadata, and backup identity before changing services.
2. Verify the selected dump checksum and readability.
3. Restore into a disposable isolated TimescaleDB target first when time
   permits, with no production volume, network, port, or credentials.
4. Review the restored migration chain and required application tables, then
   run the current backend and worker schema checks against the isolated target.
5. Obtain explicit operator authorization for any production restore.
6. Restore only to the authorized target using the guarded restore script. The
   script requires `--source`, `--target-db`, `--confirm-replace`, and refuses
   the configured database unless its additional production override is
   supplied.
7. Recheck migration history, tenant/membership and owner columns, outboxes,
   archive structures, DB/Valkey connectivity, application readiness, and all
   worker heartbeats.
8. Re-enable writes gradually and preserve the pre-restore evidence.

Example for an isolated target only:

```bash
./scripts/restore_database.sh \
  --source /path/to/verified.dump \
  --target-db logsentinel_restore \
  --confirm-replace
POSTGRES_DB=logsentinel_restore ./scripts/verify_restore.sh
```

A restore recovers the database only. It does not restore Valkey streams,
consumer pending-entry state, parser cache, or external provider state. Writes
after the selected backup timestamp are lost from the restored database; record
that recovery-point limitation before authorization. Never use a database
restore to roll back a source-only application defect.

### Valkey recovery and reconciliation

The production Compose contract uses a persistent `valkey_data` volume with
AOF enabled, `appendfsync everysec`, an RDB save trigger of `60 1`, and an
`unless-stopped` restart policy. A surviving volume can therefore be restarted
and checked for stream, consumer-group, and pending-entry state. PostgreSQL is
the authority for committed logs, ownership, pipeline ledger, outbox, feature
inputs, incidents, and archive metadata. Valkey is transport/pending work,
heartbeat, parser/cache, and session/reset state according to the worker.

If the Valkey volume is lost, start a clean Valkey instance and allow current
workers to recreate their consumer groups. Do not flush or replace the
production instance as a test. Reconcile against PostgreSQL durable rows and
expect unsynchronized pending transport entries, cache values, heartbeats, and
external provider outcomes to be rebuilt, expired, or rechecked as applicable.
Never infer that a PostgreSQL restore recovers a Valkey PEL.

06C.1 records a read-only production inspection of AOF, `appendfsync`, RDB
save configuration, `noeviction`, persistent `/data` volume, last persistence
status, AOF rewrite status, and stream/group/XPENDING state. The disposable 06C
same-volume restart and total-loss reconciliation evidence is the recovery
proof. Total Valkey loss does not restore the PEL; workers rebuild transport
state and reconcile against PostgreSQL durable rows.

### PITR decision and recovery chain

Before using PITR, inspect the actual target instance:

```bash
  docker exec logsentinel-timescaledb-1 psql -X -U logsentinel -d logsentinel_db \
  -v ON_ERROR_STOP=1 -c 'SHOW wal_level' \
  -c 'SHOW archive_mode' \
  -c 'SHOW archive_command' \
  -c 'SHOW archive_timeout' \
  -c 'SHOW max_wal_size' \
  -c 'SHOW checkpoint_timeout'
```

Before activation or any future configuration change, require a current valid
local and remote logical backup, a source/Compose/PostgreSQL configuration
snapshot, a known rollback package, a capacity check, and a healthy pre-change
snapshot. PostgreSQL `archive_mode` requires a controlled PostgreSQL restart;
restart only the database service with `--no-deps`, preserve the
`timescale_data` volume, and verify `pg_isready`, TimescaleDB, migration head,
all dependent services, archiver statistics, and remote WAL objects afterward.

The valid PITR chain is **physical base backup plus remote WAL after that base**.
The logical `pg_dump` path alone is never described as PITR. To create a
physical base in the current Compose deployment:

```bash
COMPOSE_PROFILES=pitr-base-backup docker compose --env-file .env \
  -f docker-compose.prod.yml run --rm --no-deps pitr-base-backup
```

The helper checks database size and free space before invoking
`pg_basebackup -Ft -z -X stream --manifest-checksums=SHA256`; it removes only
its own temporary staging directory after verified remote upload. The
manifest-last convention means a recovery point is not discoverable as complete
until every base file has passed remote HEAD verification. Retrieve a verified
point only into disposable storage with:

```bash
python3 scripts/retrieve_physical_base_backup.py <base-id> /tmp/pitr-base
```

Then extract and recover it in an isolated PG16/TimescaleDB target using the
repository WAL restore helper. Never point this procedure at the live
`timescale_data` volume. Remote WAL retention is intentionally not auto-purged
until the separate DATA-003 retention owner approves a bounded policy; local
PostgreSQL WAL remains governed by `max_wal_size` and checkpoint behavior.

### Post-restore security and readiness checks

Run these checks against the isolated target before any authorized production
restore. Repeat them against the authorized target after restore:

```bash
python scripts/database_lifecycle.py --validate
docker exec <target-postgres> psql -X -v ON_ERROR_STOP=1 -U logsentinel -d <target-db> \
  -c "SELECT COUNT(*) AS ownerless_logs FROM logs WHERE owner_user_id IS NULL" \
  -c "SELECT COUNT(*) AS tenant_owner_mismatches FROM logs l JOIN users u ON u.id=l.owner_user_id WHERE l.tenant_id<>u.tenant_id" \
  -c "SELECT COUNT(*) AS orphan_pipeline_rows FROM pipeline_outbox p LEFT JOIN users u ON u.id=p.owner_user_id WHERE p.owner_user_id IS NOT NULL AND (u.id IS NULL OR p.tenant_id<>u.tenant_id)"
```

Then start the current backend and workers in the approved order, confirm
`/health` and `/readiness`, worker DB/schema gates, heartbeats, pipeline
consumer-group state, and zero provider side effects in the isolated test.
Prefer a forward fix when the application version is incompatible with the
restored schema; use database restore when durable data must be rolled back.

## Deployment and rollback

Do not build or deploy from `/home/ubuntu/LogSentinel` as an implicit current
directory. A deployment candidate must come from the reviewed runtime source
manifest, deterministic source artifact, immutable image IDs, SBOM, Trivy scan,
release manifest, rollback contract, and `release-ready` result.

1. Confirm the release ID and source manifest SHA from the approved release
   evidence. Keep staging timestamped and isolated from the active Compose
   project.
2. Verify the transferred source and OCI image archive SHA-256 values, extract
   the source artifact into a clean staging directory, and load the image
   archive. These steps must not recreate a service.
3. Run the read-only server predeploy verifier. Require matching artifact and
   staged source hashes, release-labelled immutable image IDs, SBOM and Trivy
   identities, migration compatibility, and all previous rollback images.
   `FAIL` or `UNKNOWN` means stop.
4. Require a `PASS` release-gate result and a second server verification with
   `--require-release-gate-result`. The gate result does not authorize or
   perform deployment.
5. Separately invoke the controlled OCI deployment using the verified image
   identities and reviewed Compose configuration. Never run Compose `build`
   against the development checkout. Preserve the previous backend, worker,
   and Caddy images; do not prune or remove rollback images.
6. Recreate services sequentially with health gates. Keep PostgreSQL and Valkey
   untouched unless the reviewed release specifically requires a separately
   authorized database change. Never use `docker compose down` for a normal
   release.
7. Observe at least 120 seconds of repeated health samples. Require backend
   health/readiness, PostgreSQL, Valkey, worker health and heartbeats, zero PEL
   and lag, unchanged DLQ quarantine, Caddy/frontend, Prometheus, Alertmanager,
   backup timer, PITR/WAL, and migration identity.
8. Run `scripts/verify_release.py`; require source identity, all active image
   IDs, SBOM linkage, Trivy policy, migration, host-only retention tooling, and
   release-gate evidence to pass. Only then record the current and previous
   known-good markers with `scripts/record_production_release.py`.

Rollback uses the recorded previous backend, worker, and Caddy immutable image
IDs plus the recorded Compose/source identity. Verify compatibility with the
current migration before rollback. Prefer a forward fix when a release has
already written a newer schema or durable data. Stop writes and traffic when
correctness, authorization, migration, or data integrity is in doubt. A
database restore requires explicit authorization, an isolated restore check
where possible, and acceptance that post-backup writes are lost. Keep older
rollback evidence while the retention policy remains pending.

Never rerun completed migrations, edit migration history/checksums, or paste
secret values into shell history. `Cloudflare Pages` remains separately
managed and must be verified/deployed through its own authorized workflow.

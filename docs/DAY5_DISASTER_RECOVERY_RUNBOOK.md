# Day 5: Database Migrations, Backup & Disaster Recovery (DR)

This runbook defines repository-supported recovery procedures. The 05D OCI
rehearsal demonstrated a disposable logical PostgreSQL restore. 06C added
repeatable disposable proof for remote recovery-point verification, restore
from the remote object, PITR mechanics, Valkey persistence/loss behavior, and a
combined application recovery. 06C.1 activated and verified the production
backup scheduler, remote WAL archival, and a physical PITR base/recovery chain.
Do not treat disposable timing as production RPO/RTO.

## 06C.1 operating state

- Production backup scheduler: repository-owned systemd service/timer installed,
  enabled, active, and tested through the exact scheduled service path.
- Approved schedule: daily at 02:00 UTC plus up to a 15-minute randomized
  delay; `Persistent=true` handles missed-run semantics.
- Production PostgreSQL PITR: `archive_mode=on`, `wal_level=replica`, and
  remote WAL archival verified; `archive_timeout` and archiver statistics are
  recorded in the 06C.1 evidence.
- Physical PITR base: created through the repository-owned PG16
  `pitr-base-backup` profile, remotely uploaded with a manifest-last contract,
  and verified before use.
- Production Valkey: runtime AOF/RDB/persistence volume and stream/group/PEL
  state inspected read-only.
- Approved RPO: **24 hours**.
- Approved RTO: **60 minutes**.
- Active backup failure detection: service exit, journal, `backup_status.py`,
  and PostgreSQL archiver statistics. Active alert delivery remains a separate
  monitoring finding and is not claimed here.

The repeatable commands are:

```bash
python scripts/integration/remediation_06c_migration_harness.py
python scripts/integration/remediation_06c_dr_harness.py
```

They use only disposable Docker infrastructure and write redacted evidence to
`temporary-report/remediation-06c/evidence/`.

---

## 🏗️ Step 1: Migration Idempotency & Schema Validation

LogSentinel's schema is currently maintained in `scripts/init.sql`. This file is designed to be fully idempotent, utilizing `IF NOT EXISTS` clauses for all tables, hypertables, and materialized views.

### Action Required: Pre-Flight Validation
Before applying schema updates to production, validate idempotency on a populated staging replica:

1. **Use the lifecycle owner (do not apply init.sql directly to a populated production database):**
   ```bash
   python scripts/database_lifecycle.py --validate
   ```
2. **Verify Continuous Aggregates (CAGGs):**
   Ensure the `logs_rollup_1m` materialized view and its refresh policy were not disrupted or rebuilt from scratch.
   ```sql
   SELECT job_id, schedule_interval, config
   FROM timescaledb_information.jobs
   WHERE application_name LIKE 'Refresh Continuous Aggregate%';
   ```
3. **Verify Compression Policies:**
   Confirm that older chunks of the `parsed_logs` hypertable are still actively compressed.
   ```sql
   SELECT hypertable_name, uncompressed_total_bytes, compressed_total_bytes
   FROM timescaledb_information.compressed_hypertable_stats;
   ```

---

## 💾 Step 2: Automated Backup Schedules

High-volume telemetry data requires both a logical recovery point and a valid
physical base plus WAL chain.

### 1. TimescaleDB (PostgreSQL) Backups
The approved logical path uses the PG16 backup profile and the host systemd
timer. Production PITR uses the custom PG16/TimescaleDB image and the
repository-owned `wal_archive` helper to upload each WAL object to the separate
`logsentinel/wal/` prefix over HTTPS. `archive_timeout=300s` is the technical
upper-bound target under active archiving; it does not replace the approved
24-hour RPO.

The repository-owned `pitr-base-backup` profile uses `pg_basebackup -Ft -z -X
stream --manifest-checksums=SHA256`, checks free space first, verifies each
remote object, and publishes a manifest last. The logical dump alone is not a
PITR base. Remote WAL/base retention remains an explicit DATA-003 policy
boundary until an owner approves a bounded purge policy.

### 2. Valkey (Redis) State Persistence
Valkey holds the critical state for Drain3 parsing templates, Stream PELs, and alert deduplication.
* **Recommendation:** Ensure Valkey is configured with both **RDB (Snapshotting)** and **AOF (Append-Only File)** persistence enabled.
* **Action:** In your Valkey `redis.conf` or Helm values, verify:
  ```conf
  appendonly yes
  appendfsync everysec
  save 60 1
  ```

---

## 🔄 Step 3: Cold Restore Disaster Recovery Drill

Your DR strategy is only as good as your last tested restore. Execute the
following drill in a staging or disposable isolated environment:

### 1. Automated Backups
LogSentinel provides an automated backup script that captures logical schema, hypertable state, and uploads directly to S3.
* **Run Backup:**
  ```bash
  ./scripts/backup_database.sh
  ```
* This script creates a custom-format dump, checksum, and manifest. Remote
  upload is required by default and uses `S3_BUCKET`, `S3_ENDPOINT`, and
  `S3_REGION`.

### 2. The Drill:
1. **Use a disposable target:** The restore script requires `--source`,
   `--target-db`, and `--confirm-replace`; it refuses the configured database
   unless an additional explicit override is set.
2. **Restore TimescaleDB:**
   * Run the restore script, passing the backup filename:
     ```bash
     ./scripts/restore_database.sh --source logsentinel_YYYYMMDDTHHMMSSZ.dump --target-db logsentinel_restore --confirm-replace
     ```
   * The script will automatically fetch it from S3 if it is not available locally.
   * It verifies the checksum and required schema/migration markers through
     `scripts/verify_restore.sh`.
3. **Restore Valkey:**
   * This is not covered by the PostgreSQL logical backup. Use a separately
     approved Valkey persistence/restore procedure if Valkey recovery is in
     scope; do not claim that a database restore recovers streams, PELs, or
     parser state.
4. **Validation:**
   * Start the `archive-worker` and `backend` deployments.
   * Verify that the Drain3 parser successfully reloads the `drain3:state:snapshot` from Valkey.
    * Verify that the sidecar reconciliation helper runs to ensure `archive_manifest` consistency.

### 3. Physical PITR chain recovery

For a point-in-time recovery, select a complete physical base manifest and a
target after that base. Retrieve the base only into isolated storage:

```bash
python3 scripts/retrieve_physical_base_backup.py <base-id> /tmp/pitr-base
```

Extract `base.tar.gz` into a new PG16/TimescaleDB data directory and extract the
streamed `pg_wal.tar.gz` into that directory's `pg_wal/` subdirectory (the tar
entries are root-relative). Then create `recovery.signal` and configure:

```conf
restore_command = '/usr/local/bin/logsentinel-wal-restore.sh "%f" "%p"'
recovery_target_action = 'promote'
```

Pass the remote-storage environment to the isolated target without printing it.
The restore helper verifies remote size and SHA-256 metadata before returning
success. Validate PostgreSQL startup, TimescaleDB, migration head,
owner/tenant invariants, and current application schema. Never run this against
the production `timescale_data` volume. The production destructive PITR drill
is intentionally not performed; 06C disposable PITR mechanics plus this
production-generated chain are the evidence boundary.

---

## ✅ Day 5 Sign-Off Checklist
- [ ] Lifecycle validation executed against staging with zero errors.
- [x] `./scripts/backup_database.sh` is on the approved 02:00 UTC daily systemd
  schedule with up to 15 minutes of randomized delay and remote verification.
- [x] Physical PG16 base backup plus remote WAL chain is active and verified;
  the logical dump is not incorrectly used as the PITR base.
- [x] Valkey persistence runtime state and the authoritative/rebuildable
  recovery boundary are verified separately.
- [x] 05D cold restore rehearsal completed with an explicit disposable target
  and confirmed schema recovery.
- [x] Approved RPO is 24 hours and approved RTO is 60 minutes; the 06C
  27.571-second disposable rehearsal is not production recovery time.

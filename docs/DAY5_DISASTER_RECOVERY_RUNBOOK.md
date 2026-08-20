# Day 5: Database Migrations, Backup & Disaster Recovery (DR)

This runbook defines the strategies for safeguarding LogSentinel's telemetry data, ensuring that database schema migrations are zero-downtime and that the system can recover from catastrophic data loss.

---

## 🏗️ Step 1: Migration Idempotency & Schema Validation

LogSentinel's schema is currently maintained in `scripts/init.sql`. This file is designed to be fully idempotent, utilizing `IF NOT EXISTS` clauses for all tables, hypertables, and materialized views.

### Action Required: Pre-Flight Validation
Before applying schema updates to production, validate idempotency on a populated staging replica:

1. **Dry-Run the Script:**
   ```bash
   psql -h staging-db -U logsentinel -d logsentinel_db -f scripts/init.sql
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

High-volume telemetry data requires robust, point-in-time recovery mechanisms.

### 1. TimescaleDB (PostgreSQL) Backups
For production, standard `pg_dump` is often insufficient for TimescaleDB hypertables due to sheer volume.
* **Recommendation:** Deploy **pgBackRest** or **WAL-G** as a sidecar or cronjob to stream Write-Ahead Logs (WAL) continuously to AWS S3, Google Cloud Storage, or Azure Blob Storage.
* **Retention Policy:** Keep full base backups weekly and incremental WAL archives for 30 days (matching our raw data retention policy).

### 2. Valkey (Redis) State Persistence
Valkey holds the critical state for Drain3 parsing templates, Stream PELs, and alert deduplication.
* **Recommendation:** Ensure Valkey is configured with both **RDB (Snapshotting)** and **AOF (Append-Only File)** persistence enabled.
* **Action:** In your Valkey `redis.conf` or Helm values, verify:
  ```conf
  appendonly yes
  appendfsync everysec
  save 900 1
  save 300 10
  ```

---

## 🔄 Step 3: Cold Restore Disaster Recovery Drill

Your DR strategy is only as good as your last tested restore. Execute the following drill in a staging cluster:

### The Drill:
1. **Wipe the Database:** Drop the entire `logsentinel_db` and delete the Valkey persistent volume.
2. **Restore TimescaleDB:**
   * Provision a fresh TimescaleDB instance.
   * Restore the latest base backup and replay the WAL logs to a specific point-in-time using pgBackRest.
3. **Restore Valkey:**
   * Place the latest `dump.rdb` and `appendonly.aof` files into the Valkey data directory before pod startup.
4. **Validation:**
   * Start the `drain-worker` and `event-worker` deployments.
   * Verify that the Drain3 parser successfully reloads the `drain3:state:snapshot` from Valkey.
   * Verify that TimescaleDB background jobs automatically resume indexing and compressing the restored chunks.

---

## ✅ Day 5 Sign-Off Checklist
- [ ] `scripts/init.sql` executed against staging with zero errors or locked tables.
- [ ] TimescaleDB WAL archiving (e.g., pgBackRest) configured and pushing to remote object storage.
- [ ] Valkey AOF + RDB persistence enabled.
- [ ] Cold restore drill performed successfully with confirmed Drain3 state recovery.

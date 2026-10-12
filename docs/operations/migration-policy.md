# Supported upgrade policy

This policy is the operational boundary for `MIG-001`. It does not claim that
every historical database can be upgraded automatically, and it never infers
an owner or tenant for arbitrary pre-tenant operational rows.

## Decision categories

| Category | Meaning | Required operator action |
|---|---|---|
| **SUPPORTED AUTOMATIC UPGRADE** | Empty databases or databases with the canonical bootstrap and owner-aware lifecycle markers. | Run the repository-owned `database_lifecycle.py --validate`/`--apply` path. Never replay legacy SQL by filename discovery. |
| **SUPPORTED WITH APPROVED LEGACY ADOPTION** | The known post-tenant historical shape used by the reviewed legacy bridge and later 06C fixtures. | Inventory rows first, use only the approved six-account adoption mapping where applicable, stage the forward path, verify checksums and ownership invariants, and record operator approval. |
| **SAFE-STOP — MANUAL DATA OWNERSHIP DECISION REQUIRED** | The pre-tenant-partitioning shape or any shape whose operational rows do not carry authoritative tenant/owner identity. | Stop before migration. Preserve the database, produce an ownership inventory, and obtain a manual data-ownership decision. Do not guess, bulk-adopt, or run the frozen destructive cutover. |
| **DATABASE RESTORE / FORWARD-FIX REQUIRED** | A partially recorded or newer schema where the current application/migration contract is not known to be compatible. | Restore a known compatible database/application pair or obtain a separately reviewed forward fix. Do not use source-only rollback against newer durable data. |
| **UNSUPPORTED DIRECT UPGRADE** | An unknown schema, missing lifecycle identity, or historical migration path outside the reviewed families. | Refuse direct upgrade and escalate for a dedicated migration design and disposable rehearsal. |

## Representative historical families

The 06C disposable evidence maps the reviewed families as follows:

| Historical family | Policy category | Evidence boundary |
|---|---|---|
| Current canonical bootstrap, fresh install | SUPPORTED AUTOMATIC UPGRADE | Canonical bootstrap plus all active lifecycle entries and current application gates pass. |
| `legacy-bridge-to-current` | SUPPORTED WITH APPROVED LEGACY ADOPTION | The approved bridge identity set and ownership checks passed in disposable infrastructure. |
| `pre-distributed-correctness`, `pre-durable-webhook`, `pre-release-gates`, `pre-tenant-authority`, `pre-pipeline-durability`, `pre-per-user-ownership` | SUPPORTED AUTOMATIC UPGRADE after the reviewed bridge state | Each bounded 06C fixture converged through `20260913_0010_per_user_data_ownership`; this does not generalize to unreviewed data shapes. |
| `pre-per-user-full-data-safety-stop` | SAFE-STOP — MANUAL DATA OWNERSHIP DECISION REQUIRED | The 0010 guard stopped before destructive cleanup and left the 3,153 logs and derived rows unchanged. |
| `pre-tenant-partitioning` / the frozen `20260826_0001_multitenant_partitioning` cutover | SAFE-STOP — MANUAL DATA OWNERSHIP DECISION REQUIRED | The historical cutover rewrites the hypertable and assigns default tenant ownership; it is frozen and must not be replayed. |
| Current database with an older application binary | DATABASE RESTORE / FORWARD-FIX REQUIRED | Compatibility of the old binary with newer durable schema/data is not assumed. |
| Any shape not listed above | UNSUPPORTED DIRECT UPGRADE | Stop and obtain a representative fixture and review. |

## Pre-tenant historical boundary

The pre-tenant family uses `created_at` as its operational time shape and lacks
the authoritative tenant/owner partitioning required by the current
application. The published `20260826_0001_multitenant_partitioning.sql` file
recreates the `logs` hypertable and assigns default tenant values during a
destructive transition. It is retained only as frozen history for checksum
validation; the current lifecycle intentionally refuses to execute it when it
is not already recorded.

If such a database is encountered:

1. stop the lifecycle before any DDL or deletion;
2. preserve the original database and collect schema/row-count metadata without
   copying operational payloads into logs;
3. identify the owner and tenant of each historical family with the operator;
4. either perform a separately approved, staged adoption with an auditable
   mapping, or restore/forward-fix from a known compatible point; and
5. verify zero ownerless rows, tenant/owner consistency, migration checksums,
   and current application compatibility before accepting the result.

No automatic migration may convert arbitrary default-tenant rows into current
owner records. A fail-closed stop is the expected safe result.

## Detector and lifecycle relationship

Run the read-only detector before a migration operation:

```bash
python scripts/migration_preflight.py --json
```

The detector reports schema markers, relevant `logs` columns, migration head,
and one of the categories above. It performs no DDL, data update, delete,
ledger write, ownership adoption, or migration replay. The lifecycle remains
the only migration executor and independently enforces the canonical bootstrap,
checksum, ledger-gap, and frozen-history guards.

`MIG-001` remains **PARTIALLY CLOSED**: the reviewed families and the supported
upgrade boundary are clearer, but arbitrary historical full-data convergence and
the destructive pre-tenant cutover are intentionally not claimed safe.

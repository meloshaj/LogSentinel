# Retention, release provenance, and mandatory gates

## Authority boundary

`config/retention-policy.yml` is the only retention-policy source. The checked-in
policy is a proposal with `approval.status: pending`, no effective date, and
`mode.destructive_apply_enabled: false`. Durations in that file are proposed
values, not operator approval. Missing or malformed policy input fails closed.

The current supported production posture is plan-only. Install
`deploy/systemd/logsentinel-retention-plan.{service,timer}` only when a
read-only production plan is desired. The timer has no `--apply` flag. A
destructive run requires an approved policy, a policy-level active mode, and an
explicit `scripts/retention.py --apply` invocation.

Do not put secrets, tokens, object keys, customer content, or personal
approval identity data in the policy or plan output.

## Database retention

`scripts/retention.py` obtains the UTC cutoff from PostgreSQL, inventories the
actual schema and foreign keys, takes the stable PostgreSQL advisory lock
`logsentinel-retention-v1`, and processes bounded CTE batches. Each batch is a
transaction. The dependency order is:

1. anomaly events and tracking loops;
2. feature windows;
3. resolved incidents;
4. feature inputs and terminal outbox rows;
5. pipeline ledger;
6. raw logs.

Active leases, non-terminal delivery rows, open incidents, and security state
are protected. Password-reset rows are a separate class and may only ever
consider `completed`, `expired`, or `invalidated` rows after a separately
approved policy; `issued` and non-expired capabilities are never candidates.
The migration ledger is retained forever. The old `cleanup_storage` entrypoint
now delegates to this engine and no longer accepts an interpolated day count or
issues an unbounded delete.

Archive object deletion requires object existence, manifest existence, checksum
verification, database-reference consistency, and no active rehydration. The
database metadata and object are handled as one reviewed candidate; an
inconsistent pair is retained and blocks apply.

The archive worker no longer runs a final-purge loop. Archive hot-to-cold
movement remains a separate verified state machine. Final purge is controlled
by the explicit retention command.

## Recovery assets

`scripts/recovery_retention.py` uses only manifest metadata. Logical backups
require a valid manifest, checksum, remote verification, and a minimum of three
valid recovery points in the proposal. The newest valid point is always
protected. Physical PITR bases require a valid independently usable newer base
and retain the configured minimum. WAL eligibility is derived from the retained
base chain; an unknown chain dependency blocks apply. No `delete older than N`
rule exists for WAL or physical bases.

Backup creation never deletes recovery points. Its legacy cleanup switch now
emits a plan only and directs operators to the guarded recovery planner.

## Non-PostgreSQL storage

Valkey is transport/cache state, not password-reset authority. Streams are not
trimmed blindly: consumer-group catch-up, PostgreSQL convergence, DLQ policy,
and recovery-copy guards are required. The DLQ has no automatic purge policy.
Prometheus remains bounded to seven days and 512 MiB by the 07B monitoring
Compose command; this is monitoring retention only and does not close DATA-003.
Alertmanager state is persistent operator state, not application-log retention.

Rollback snapshots use count/age guards and preserve the active release, the
immediately previous known-good release, and incident-held snapshots. Staging
cleanup is prefix- and reference-validated, not a broad wildcard delete. The
plan/apply implementation is `scripts/local_retention.py`; an apply invocation
must provide current active and deployment-referenced paths and still requires
an approved active policy.

## Retention observability

The scheduler units shipped by 07D are plan-only and are not installed or
enabled by this remediation. Their output is bounded structured metadata. If
an operator activates destructive retention later, expose the run timestamps,
deleted rows/bytes by storage class, errors, guard blocks, and policy version
through the approved low-cardinality operations collector and add stale,
failure, and guard-failure alerts. A zero deletion count is not an alert.

## Production release authority

**Production release authority: MANUAL VERIFIED OCI RELEASE WORKFLOW.**

GitHub is used as the source repository, for collaboration, and for backup and
version history. GitHub is not the production deployment authority, a
production credential store, or a required production CI control plane.
Automatic GitHub-to-production deployment is not supported. A GitHub-hosted
release gate is not required for production eligibility.

The remote GitHub workflow remains older, the remote release gate is not
enforced, and the main ruleset does not require that job. Those facts remain a
repository-governance observation and an accepted architectural boundary; no
remote workflow, ruleset, or branch protection was changed. The local
`.github/workflows/ci.yml` remains useful and may be published later. Production
eligibility does not depend on its remote publication.

## Canonical manual release gate

Use `python scripts/release_gate.py` for the repository-owned fail-closed
workflow. Its phases are `source-manifest`, `validate`, `build`, `security`,
`package`, `verify`, and `release-ready`. Each phase is bound to one release ID
and one source-manifest SHA. Only `PASS`, `FAIL`, and `UNKNOWN` are valid gate
states. `UNKNOWN` blocks eligibility. No warning or missing result is a pass.

The local worktree may be dirty. That does not make an implicit checkout a
release. First write and inspect an explicit runtime source manifest:

```text
python scripts/release_gate.py source-manifest \
  --source-root . --output <release-state>/source-manifest.json
```

The first result is `UNKNOWN`. Review the complete sorted runtime file list,
runtime SHA-256, and host release/retention tooling SHA-256. Repeat with both
exact digests to record the review:

```text
python scripts/release_gate.py source-manifest \
  --source-root . --output <release-state>/source-manifest.json \
  --reviewed-source-sha256 <reviewed-runtime-manifest-sha256> \
  --reviewed-host-operations-sha256 <reviewed-host-operations-sha256>
```

`validate` and `build` refuse an unreviewed or changed source or host-tool
manifest. Neither manifest contains secrets. The runtime manifest excludes
generated files. Build creates a deterministic
runtime-source tar from only listed, hashed files, then immutable backend,
worker, and Caddy images labelled with the release ID and source SHA. It also
exports a Docker image archive and records its digest. Do not build from the
current directory outside this process.

The command sequence is:

```text
python scripts/release_gate.py validate --release-id <release-id> \
  --source-root . --source-manifest <release-state>/source-manifest.json \
  --state-dir <release-state>
python scripts/release_gate.py build --release-id <release-id> \
  --source-root . --source-manifest <release-state>/source-manifest.json \
  --state-dir <release-state> --platform linux/arm64
python scripts/release_gate.py security --release-id <release-id> \
  --state-dir <release-state>
python scripts/release_gate.py package --release-id <release-id> \
  --state-dir <release-state> --rollback <rollback-contract.json> \
  --migration-head 20260917_0011_password_reset_atomicity
```

`validate` covers compile, Ruff format and lint, mypy, backend and root tests,
frontend tests, frontend typecheck/build, browser truth, migration lifecycle,
backup/DR, retention, production and monitoring Compose, workflow validation,
and rollback contract. A migration integration check requires an explicit
disposable loopback database; production credentials or databases are never
used by the gate.

`security` creates one Syft SPDX SBOM and one raw Trivy result per immutable
image. The policy is HIGH=0 and CRITICAL=0, `ignore-unfixed=false`, no ignore
files, and no CVE exceptions. Both the raw result and SBOM are SHA-256 linked to
their image ID in `release-manifest.json`.

## Server predeploy and eligibility

Transfer the complete release directory into a timestamped production staging
directory. Verify the file transfer digest before use. Extract the runtime
source tar into a clean staging subdirectory and load the Docker image archive
with `docker load`; neither action recreates a running service. Do not transfer
`.env`, SSH keys, or other credentials in release evidence.

On the production host, run the read-only server verifier against that exact
staging directory and extracted source. It rechecks the artifact SHA, archived
and extracted source file set/hashes, immutable candidate image IDs and source
labels, SBOM and Trivy file hashes and image links, scan policy, migration head,
host-only retention and release-tool identities, and presence of rollback
images and metadata. Pass `--host-root /home/ubuntu/LogSentinel` to bind the
host-tool check to the installed operational tooling. Any mismatch is `FAIL`;
absent evidence or an unavailable Docker identity is `UNKNOWN`. Stop in either
case.

After the server-side candidate verification passes, run:

```text
python scripts/release_gate.py release-ready --release-id <release-id> \
  --state-dir <release-state> \
  --result-output <release-state>/release-gate-result.json
```

`release-ready` requires every mandatory phase and control to be `PASS`. It
records source and artifact digests, three image IDs, SBOM and Trivy hashes and
counts, migration head, test states, rollback identities, timestamp, and
eligibility. It always records `deployment_authorized: false` and never deploys.
Copy the completed manifest and result into the staging bundle, then run the
server verifier again with `--require-release-gate-result`. That final
read-only check must match the release-gate result hash and release identity
before an operator separately invokes the deployment procedure.

The manual deployment remains direct controlled OCI deployment. Use the
staged immutable identities, the reviewed production Compose configuration,
sequential service recreation, health gates, and preserved rollback images.
Never use `docker compose build` or deploy a mutable tag from a working
directory. Eligibility does not trigger a deployment or a background action.

## Postdeployment release completion

After the operator's deployment invocation, run the production verifier and
collect at least 120 seconds of repeated health samples. Completion requires
backend health and readiness; PostgreSQL; Valkey; all worker healthchecks and
heartbeats; zero PEL and lag; unchanged known DLQ quarantine; Caddy and frontend;
Prometheus and Alertmanager; active backup timer and healthy PITR/WAL; migration
identity; retention tooling identity; SBOM and scan linkage; and the release
gate result. Any material failure means the release is not complete.

`scripts/verify_release.py` is read-only. It verifies the active backend,
worker, and Caddy image IDs; source identity; migration; SBOM hashes and image
links; raw Trivy results and HIGH/CRITICAL policy; host-only retention and
release-tool hashes; and the release-gate evidence hash. It returns only
`PASS`, `FAIL`, or `UNKNOWN`, and prints no environment values.

Only after that verifier and the health window pass, run
`scripts/record_production_release.py`. It atomically writes a non-secret
`current-release.json` marker and a `previous-known-good.json` marker under the
operator-selected release metadata directory. Each marker records the release
ID, source manifest SHA, artifact and OCI archive digests, active image IDs,
migration head, deployment time, rollback identity, and evidence hashes. It
does not deploy or delete images or old evidence. Keep older rollback images
and evidence until retention has a separately approved policy.

The checked-in `scripts/release_manifest.py` remains the canonical manifest
schema. A GitHub run ID is not required. SHA-256, immutable Docker image IDs,
SBOM, Trivy, manifest, and production verifier remain required provenance.
Cryptographic signing is optional and unapproved; do not generate a signing
key.

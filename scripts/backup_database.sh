#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/backups}"
BACKUP_CLEANUP_ENABLED="${BACKUP_CLEANUP_ENABLED:-false}"
REQUIRE_REMOTE_BACKUP="${REQUIRE_REMOTE_BACKUP:-true}"
S3_BUCKET="${S3_BUCKET:-${S3_BACKUP_BUCKET:-${S3_BUCKET_NAME:-}}}"
S3_ENDPOINT="${S3_ENDPOINT:-${S3_ENDPOINT_URL:-}}"
S3_REGION="${S3_REGION:-}"
S3_SERVER_SIDE_ENCRYPTION="${S3_SERVER_SIDE_ENCRYPTION:-AES256}"
BACKUP_EXECUTION_VERSION="logsentinel-backup-v2"
BACKUP_EXECUTION_SOURCE="scripts/backup_database.sh"

if [[ "$REQUIRE_REMOTE_BACKUP" != "true" && "$REQUIRE_REMOTE_BACKUP" != "false" ]]; then
  echo "REQUIRE_REMOTE_BACKUP must be true or false" >&2
  exit 2
fi

if [[ "$BACKUP_CLEANUP_ENABLED" != "true" && "$BACKUP_CLEANUP_ENABLED" != "false" ]]; then
  echo "BACKUP_CLEANUP_ENABLED must be true or false" >&2
  exit 2
fi
if [[ "$REQUIRE_REMOTE_BACKUP" == "true" ]]; then
  : "${S3_BUCKET:?S3_BUCKET is required when REQUIRE_REMOTE_BACKUP=true}"
  : "${S3_REGION:?S3_REGION is required when remote backup is enabled}"
fi

require_command() {
  local command_name="$1"
  local error_message="$2"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$error_message" >&2
    exit 4
  fi
}

require_command pg_dump "PostgreSQL backup client pg_dump is required"
require_command psql "PostgreSQL client psql is required"
require_command pg_restore "PostgreSQL restore utility pg_restore is required"
require_command python3 "Python 3 is required for backup manifest generation"

if ! client_version_output="$(pg_dump --version 2>/dev/null)"; then
  echo "Unable to determine PostgreSQL backup client version" >&2
  exit 4
fi
if [[ ! "$client_version_output" =~ PostgreSQL\)?[[:space:]]+([0-9]+)(\.[0-9]+)? ]]; then
  echo "Unable to determine PostgreSQL backup client major version" >&2
  exit 4
fi
client_major="${BASH_REMATCH[1]}"

export PGPASSWORD="${POSTGRES_PASSWORD:-}"
if ! server_version="$(psql -XAt --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c 'SHOW server_version' 2>/dev/null)"; then
  echo "Unable to determine PostgreSQL server version" >&2
  exit 4
fi
if [[ ! "$server_version" =~ ^[[:space:]]*([0-9]+)(\.[0-9]+)? ]]; then
  echo "Unable to determine PostgreSQL server major version" >&2
  exit 4
fi
server_major="${BASH_REMATCH[1]}"

if (( client_major != server_major )); then
  echo "PostgreSQL backup client major version mismatch" >&2
  exit 5
fi

mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
backup_file="${BACKUP_DIR}/logsentinel_${POSTGRES_DB}_${timestamp}.dump"
checksum_file="${backup_file}.sha256"
manifest_file="${backup_file}.manifest.json"
backup_partial="${backup_file}.partial"
checksum_partial="${checksum_file}.partial"
manifest_partial="${manifest_file}.partial"
trap 'rm -f -- "$backup_partial" "$checksum_partial" "$manifest_partial"' EXIT

echo "PostgreSQL backup preflight passed: server major ${server_major}, pg_dump major ${client_major}"
echo "Creating logical backup for database ${POSTGRES_DB} at ${timestamp}"
if ! pg_dump \
  --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --format custom --compress 6 --no-owner --no-acl \
  --file "$backup_partial" 2>/dev/null; then
  echo "PostgreSQL backup command failed" >&2
  exit 6
fi
if [[ ! -s "$backup_partial" ]]; then
  echo "PostgreSQL backup produced an empty dump" >&2
  exit 6
fi
if ! pg_restore --list "$backup_partial" >/dev/null 2>&1; then
  echo "PostgreSQL backup failed structural validation" >&2
  exit 6
fi
mv "$backup_partial" "$backup_file"
if ! python3 - "$backup_file" "$checksum_partial" <<'PY'
import hashlib
import pathlib
import sys

backup, checksum = map(pathlib.Path, sys.argv[1:])
digest = hashlib.sha256(backup.read_bytes()).hexdigest()
checksum.write_text(f"{digest}  {backup.name}\n", encoding="utf-8")
PY
then
  echo "PostgreSQL backup checksum creation failed" >&2
  exit 6
fi

read -r dump_sha256 _ < "$checksum_partial"
if [[ "${#dump_sha256}" -ne 64 || ! "$dump_sha256" =~ ^[0-9a-fA-F]+$ ]]; then
  echo "PostgreSQL backup checksum is invalid" >&2
  exit 6
fi

database_version="$server_version"
timescaledb_version="$(psql -XAt --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "SELECT COALESCE((SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'), 'not-installed')" 2>/dev/null || printf 'unknown')"
migration_version="$(psql -XAt --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "SELECT COALESCE(MAX(version), 'bootstrap') FROM schema_migrations" 2>/dev/null || printf 'unknown')"
script_path="${BASH_SOURCE[0]}"
script_sha256="$script_path"
python3 - "$manifest_partial" "$timestamp" "$POSTGRES_DB" "$database_version" "$server_major" "$client_major" "$timescaledb_version" "$migration_version" "$backup_file" "$checksum_partial" "$BACKUP_EXECUTION_SOURCE" "$BACKUP_EXECUTION_VERSION" "$script_sha256" <<'PY'
import hashlib
import json
import pathlib
import sys

(
    manifest,
    timestamp,
    database_logical_identifier,
    database_version,
    server_major,
    client_major,
    timescaledb_version,
    migration_version,
    backup,
    checksum,
    execution_source,
    execution_version,
    script_sha256,
) = sys.argv[1:]
digest = pathlib.Path(checksum).read_text(encoding="utf-8").split()[0]
payload = {
    "created_at_utc": timestamp,
    "database_logical_identifier": database_logical_identifier,
    "database_version": database_version,
    "server_postgresql_major": int(server_major),
    "pg_dump_major": int(client_major),
    "timescaledb_version": timescaledb_version,
    "migration_head": migration_version,
    "backup_filename": pathlib.Path(backup).name,
    "dump_filename": pathlib.Path(backup).name,
    "sha256": digest,
    "dump_sha256": digest,
    "backup_execution_source": execution_source,
    "backup_execution_version": execution_version,
    "backup_script_sha256": hashlib.sha256(pathlib.Path(script_sha256).read_bytes()).hexdigest(),
}
pathlib.Path(manifest).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

mv "$checksum_partial" "$checksum_file"
mv "$manifest_partial" "$manifest_file"

if [[ -n "$S3_BUCKET" ]]; then
  : "${S3_REGION:?S3_REGION is required when S3_BUCKET is set}"
  export S3_BUCKET S3_ENDPOINT S3_REGION S3_SERVER_SIDE_ENCRYPTION
  python3 - "$backup_file" "$checksum_file" "$manifest_file" <<'PY'
import datetime as dt
import hashlib
import os
import pathlib
import sys

try:
    from app.security.redaction import sanitize_exception_text
except ModuleNotFoundError:
    # Repository contract tests execute from the source root; the production
    # image runs with /app as its working directory.
    from backend.app.security.redaction import sanitize_exception_text


def digest(path: pathlib.Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


try:
    import boto3

    backup, checksum, manifest = sys.argv[1:]
    paths = [pathlib.Path(backup), pathlib.Path(checksum), pathlib.Path(manifest)]
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT") or None,
        region_name=os.environ["S3_REGION"],
        aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
    )
    bucket = os.environ["S3_BUCKET"]
    sse = os.getenv("S3_SERVER_SIDE_ENCRYPTION", "AES256")
    for path in paths:
        path_digest = digest(path)
        key = f"backups/{path.name}"
        client.upload_file(
            str(path),
            bucket,
            key,
            ExtraArgs={
                "ServerSideEncryption": sse,
                "Metadata": {"sha256": path_digest},
            },
        )
        head = client.head_object(Bucket=bucket, Key=key)
        metadata = {str(k).lower(): str(v) for k, v in (head.get("Metadata") or {}).items()}
        if int(head.get("ContentLength", -1)) != path.stat().st_size:
            raise RuntimeError("remote object size mismatch")
        if metadata.get("sha256") != path_digest:
            raise RuntimeError("remote object checksum metadata mismatch")

except Exception as exc:
    # Preserve the exception class and operation context while routing all
    # provider text through the canonical bounded sanitizer.
    safe_detail = sanitize_exception_text(exc, maximum_length=1024)
    raise SystemExit(
        f"Remote backup upload or verification failed: {safe_detail}"
    )
PY
  echo "Remote upload and integrity verification passed"
elif [[ "$REQUIRE_REMOTE_BACKUP" == "true" ]]; then
  echo "Remote backup was required but no bucket was configured" >&2
  exit 3
else
  echo "Remote upload disabled explicitly; backup remains local only"
fi

if [[ "$BACKUP_CLEANUP_ENABLED" == "true" ]]; then
  # Final recovery-point deletion is never implicit in backup creation. The
  # dedicated planner enforces minimum valid points, manifest/checksum state,
  # remote verification, and chain guards before an explicit operator apply.
  echo "Backup cleanup requested; emitting a plan only. Use scripts/recovery_retention.py --apply after policy approval." >&2
  python3 /repo/scripts/recovery_retention.py \
    --policy /repo/config/retention-policy.yml \
    --directory "$BACKUP_DIR"
fi
echo "Backup completed with checksum and manifest: $(basename "$backup_file")"

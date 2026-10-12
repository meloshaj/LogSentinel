#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "Usage: $0 --source <dump path or object name> --target-db <disposable database> --confirm-replace" >&2
}

source_ref=""
target_db=""
confirm_replace="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) source_ref="${2:-}"; shift 2 ;;
    --target-db) target_db="${2:-}"; shift 2 ;;
    --confirm-replace) confirm_replace="true"; shift ;;
    *) usage; exit 2 ;;
  esac
done
[[ -n "$source_ref" && -n "$target_db" && "$confirm_replace" == "true" ]] || { usage; exit 2; }
[[ "$target_db" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "Invalid target database name" >&2; exit 2; }
if [[ "$target_db" == "${POSTGRES_DB:-}" && "${ALLOW_RESTORE_TO_CONFIGURED_DATABASE:-false}" != "true" ]]; then
  echo "Refusing to replace configured database; restore to a disposable target or set the explicit override" >&2
  exit 3
fi

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/backups}"
S3_BUCKET="${S3_BUCKET:-${S3_BACKUP_BUCKET:-${S3_BUCKET_NAME:-}}}"
S3_ENDPOINT="${S3_ENDPOINT:-${S3_ENDPOINT_URL:-}}"
S3_REGION="${S3_REGION:-}"
mkdir -p "$BACKUP_DIR"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

local_path="$source_ref"
if [[ ! -f "$local_path" ]]; then
  local_path="$BACKUP_DIR/$(basename "$source_ref")"
  if [[ ! -f "$local_path" ]]; then
    : "${S3_BUCKET:?backup not local and S3_BUCKET is not configured}"
    : "${S3_REGION:?S3_REGION is required for download}"
    export S3_BUCKET S3_ENDPOINT S3_REGION
    python3 - "$source_ref" "$local_path" <<'PY'
import os, pathlib, sys, boto3
key, local = sys.argv[1:]
if "/" not in key:
    key = f"backups/{key}"
client = boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT") or None,
    region_name=os.environ["S3_REGION"], aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"))
client.download_file(os.environ["S3_BUCKET"], key, local)
for suffix in (".sha256", ".manifest.json"):
    client.download_file(os.environ["S3_BUCKET"], key + suffix, local + suffix)
PY
  fi
fi

[[ -f "${local_path}.sha256" ]] || { echo "Checksum sidecar is required" >&2; exit 4; }
(cd "$(dirname "$local_path")" && sha256sum --check "$(basename "${local_path}.sha256")")

admin=(psql -X -v ON_ERROR_STOP=1 --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" --dbname postgres)
"${admin[@]}" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${target_db}' AND pid <> pg_backend_pid();"
"${admin[@]}" -c "DROP DATABASE IF EXISTS \"${target_db}\";"
"${admin[@]}" -c "CREATE DATABASE \"${target_db}\";"
target=(psql -X -v ON_ERROR_STOP=1 --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" --dbname "$target_db")
"${target[@]}" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
"${target[@]}" -c "SELECT timescaledb_pre_restore();"
pg_restore --exit-on-error --no-owner --no-acl --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" --dbname "$target_db" "$local_path"
"${target[@]}" -c "SELECT timescaledb_post_restore();"
POSTGRES_DB="$target_db" bash "$(dirname "$0")/verify_restore.sh"
echo "Restore and structural verification completed for explicit target ${target_db}"

#!/usr/bin/env bash
set -eo pipefail

if [ -z "$1" ]; then
    echo "Usage: $0 <backup_filename_or_path>"
    echo "Example: $0 logsentinel_backup_20260826_120000.sql.gz"
    exit 1
fi

TARGET_FILE="$1"
BACKUP_DIR=${BACKUP_DIR:-/tmp/backups}

POSTGRES_USER=${POSTGRES_USER:-logsentinel}
POSTGRES_HOST=${POSTGRES_HOST:-localhost}
POSTGRES_PORT=${POSTGRES_PORT:-5432}
POSTGRES_DB=${POSTGRES_DB:-logsentinel_db}
S3_BACKUP_BUCKET=${S3_BACKUP_BUCKET:-$S3_BUCKET_NAME}

if [ -n "$POSTGRES_PASSWORD" ]; then
    export PGPASSWORD="$POSTGRES_PASSWORD"
fi

# Resolve file locally or download from S3
LOCAL_PATH="$TARGET_FILE"
if [ ! -f "$LOCAL_PATH" ]; then
    LOCAL_PATH="${BACKUP_DIR}/${TARGET_FILE}"
    if [ ! -f "$LOCAL_PATH" ]; then
        if [ -n "$S3_BACKUP_BUCKET" ] && [ -n "$S3_ACCESS_KEY_ID" ]; then
            echo "File not found locally. Attempting to download from S3..."
            mkdir -p "$BACKUP_DIR"
            python3 -c "
import os, boto3, sys
bucket = os.environ.get('S3_BACKUP_BUCKET')
endpoint = os.environ.get('S3_ENDPOINT_URL')
file_name = os.path.basename(sys.argv[1])
local_path = sys.argv[2]

s3 = boto3.client('s3', 
    endpoint_url=endpoint,
    aws_access_key_id=os.environ.get('S3_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('S3_SECRET_ACCESS_KEY'),
    region_name='us-east-1'
)
try:
    s3.download_file(bucket, f'backups/{file_name}', local_path)
    print(f'Successfully downloaded {file_name} from s3://{bucket}/backups/')
except Exception as e:
    print(f'Failed to download from S3: {e}')
    sys.exit(1)
" "$TARGET_FILE" "$LOCAL_PATH"
        else
            echo "Error: File not found locally and S3 credentials not provided."
            exit 1
        fi
    fi
fi

echo "Initiating restore from ${LOCAL_PATH}..."

psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "postgres" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();"
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "postgres" -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\";"
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "postgres" -c "CREATE DATABASE \"${POSTGRES_DB}\";"

# Re-create timescaledb extension before restore to avoid version mismatch errors
psql -X -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
psql -X -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT timescaledb_pre_restore();"

# 2. Restore schema and data
echo "Restoring data..."
zcat "$LOCAL_PATH" | psql -X -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q || true

# Post-restore
psql -X -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT timescaledb_post_restore();"

# 3. Verify TimescaleDB extension and hypertable integrity
echo "Verifying TimescaleDB extension and hypertable..."
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    SELECT extname, extversion FROM pg_extension WHERE extname = 'timescaledb';
"
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    SELECT hypertable_name, num_dimensions, num_chunks FROM timescaledb_information.hypertables WHERE hypertable_name = 'logs';
"

# 4. Note on Sidecar Reconciliation
echo "Restore complete."
echo "Note: The sidecar reconciliation helper will automatically run to ensure 'archive_manifest' consistency when the ArchiveWorker is started."

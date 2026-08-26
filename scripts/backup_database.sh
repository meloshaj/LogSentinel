#!/usr/bin/env bash
set -eo pipefail

# Configuration
POSTGRES_USER=${POSTGRES_USER:-logsentinel}
POSTGRES_HOST=${POSTGRES_HOST:-localhost}
POSTGRES_PORT=${POSTGRES_PORT:-5432}
POSTGRES_DB=${POSTGRES_DB:-logsentinel_db}
BACKUP_DIR=${BACKUP_DIR:-/tmp/backups}
RETENTION_DAYS=${RETENTION_DAYS:-7}
S3_BACKUP_BUCKET=${S3_BACKUP_BUCKET:-$S3_BUCKET_NAME}

# Create backup directory
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/logsentinel_backup_${TIMESTAMP}.sql.gz"

echo "Starting database backup for ${POSTGRES_DB} at ${TIMESTAMP}..."

# Export PGPASSWORD if provided
if [ -n "$POSTGRES_PASSWORD" ]; then
    export PGPASSWORD="$POSTGRES_PASSWORD"
fi

# Run pg_dump
# We use logical backup (-F c is good but we'll use plain SQL with gzip as requested: .sql.gz)
pg_dump -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --exclude-schema='_timescaledb_internal' \
    --exclude-schema='_timescaledb_catalog' \
    --exclude-schema='_timescaledb_config' \
    --exclude-schema='_timescaledb_cache' \
    --exclude-schema='timescaledb_information' \
    --exclude-schema='timescaledb_experimental' \
    --clean --if-exists | gzip > "$BACKUP_FILE"

echo "Backup created locally at ${BACKUP_FILE}"

# Upload to S3 if credentials are provided
if [ -n "$S3_BACKUP_BUCKET" ] && [ -n "$S3_ACCESS_KEY_ID" ] && [ -n "$S3_SECRET_ACCESS_KEY" ]; then
    echo "Uploading backup to S3 bucket ${S3_BACKUP_BUCKET}..."
    python3 -c "
import os, boto3, sys
bucket = os.environ.get('S3_BACKUP_BUCKET')
endpoint = os.environ.get('S3_ENDPOINT_URL')
file_path = sys.argv[1]
file_name = os.path.basename(file_path)

s3 = boto3.client('s3', 
    endpoint_url=endpoint,
    aws_access_key_id=os.environ.get('S3_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('S3_SECRET_ACCESS_KEY'),
    region_name='us-east-1'
)
try:
    s3.upload_file(file_path, bucket, f'backups/{file_name}')
    print(f'Successfully uploaded {file_name} to s3://{bucket}/backups/')
except Exception as e:
    print(f'Failed to upload to S3: {e}')
    sys.exit(1)
" "$BACKUP_FILE"
else
    echo "Skipping S3 upload: S3_BACKUP_BUCKET or S3 credentials not fully configured."
fi

# Prune old local backups
echo "Pruning local backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -type f -name "logsentinel_backup_*.sql.gz" -mtime +${RETENTION_DAYS} -exec rm {} \;

echo "Backup process completed successfully."

#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

psql -X -v ON_ERROR_STOP=1 --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
DO $$
DECLARE required_table text;
BEGIN
  FOREACH required_table IN ARRAY ARRAY[
    'logs', 'tenants', 'tenant_memberships', 'pipeline_ledger',
    'pipeline_outbox', 'tenant_integrations', 'password_reset_tokens',
    'schema_migrations'
  ] LOOP
    IF to_regclass('public.' || required_table) IS NULL THEN
      RAISE EXCEPTION 'required table missing: %', required_table;
    END IF;
  END LOOP;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='logs' AND column_name='event_id'
  ) THEN
    RAISE EXCEPTION 'required logs.event_id column missing';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version='20260911_0005_distributed_correctness')
     OR NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version='20260911_0006_durable_webhook_delivery') THEN
    RAISE EXCEPTION 'required migration markers 0005/0006 missing';
  END IF;
END $$;
SQL
echo "Restore structure validated for ${POSTGRES_DB}"

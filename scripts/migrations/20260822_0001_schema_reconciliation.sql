-- LogSentinel forward migration 20260822_0001
--
-- This is the only active post-bootstrap migration.  It is deliberately
-- additive and refuses to run against an incompatible logs primary key or a
-- non-Timescale logs relation.  Historical migrations remain in the tree as
-- evidence but are not part of the executable manifest.

DO $$
DECLARE
    primary_key_columns TEXT;
BEGIN
    IF to_regclass('public.logs') IS NULL
       OR to_regclass('public.users') IS NULL
       OR to_regclass('public.feature_windows') IS NULL
       OR to_regclass('public.tracking_loops') IS NULL THEN
        RAISE EXCEPTION
            'canonical LogSentinel bootstrap is missing one or more core tables';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
          AND hypertable_name = 'logs'
    ) THEN
        RAISE EXCEPTION
            'logs is not a TimescaleDB hypertable; refusing an incompatible migration path';
    END IF;

    SELECT string_agg(att.attname, ',' ORDER BY key_columns.ordinality)
    INTO primary_key_columns
    FROM pg_index AS index_record
    JOIN pg_class AS table_record ON table_record.oid = index_record.indrelid
    JOIN pg_namespace AS namespace_record
        ON namespace_record.oid = table_record.relnamespace
    CROSS JOIN LATERAL unnest(index_record.indkey)
        WITH ORDINALITY AS key_columns(attnum, ordinality)
    JOIN pg_attribute AS att
        ON att.attrelid = table_record.oid
       AND att.attnum = key_columns.attnum
    WHERE index_record.indisprimary
      AND namespace_record.nspname = 'public'
      AND table_record.relname = 'logs';

    IF primary_key_columns IS DISTINCT FROM 'created_at,id' THEN
        RAISE EXCEPTION
            'logs primary key is %, expected canonical created_at,id; refusing ULID/integer legacy state',
            COALESCE(primary_key_columns, '<missing>');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS accounts (
    id                  BIGSERIAL       PRIMARY KEY,
    user_id             BIGINT          NOT NULL
                            REFERENCES users(id) ON DELETE CASCADE,
    provider            VARCHAR(32)     NOT NULL,
    provider_account_id VARCHAR(512)    NOT NULL,
    access_token        TEXT            NULL,
    refresh_token       TEXT            NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_accounts_provider_provider_account_id
        UNIQUE (provider, provider_account_id)
);

CREATE INDEX IF NOT EXISTS idx_accounts_user_id
    ON accounts (user_id);

CREATE TABLE IF NOT EXISTS external_identities (
    id                  BIGSERIAL       PRIMARY KEY,
    user_id             BIGINT          NOT NULL
                            REFERENCES users(id) ON DELETE CASCADE,
    provider            VARCHAR(32)     NOT NULL,
    issuer              VARCHAR(512)    NOT NULL,
    subject             VARCHAR(512)    NOT NULL,
    tenant_id           VARCHAR(128)    NULL,
    provider_object_id  VARCHAR(128)    NULL,
    email               VARCHAR(255)    NULL,
    display_name        VARCHAR(255)    NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_external_identities_provider_issuer_subject
        UNIQUE (provider, issuer, subject)
);

CREATE INDEX IF NOT EXISTS idx_external_identities_user_id
    ON external_identities (user_id);

CREATE INDEX IF NOT EXISTS idx_external_identities_lookup
    ON external_identities (provider, issuer, subject);

ALTER TABLE tracking_loops
    ADD COLUMN IF NOT EXISTS blast_radius JSONB NULL;

CREATE INDEX IF NOT EXISTS idx_logs_created_service
    ON logs (created_at DESC, service);

CREATE INDEX IF NOT EXISTS idx_tracking_loops_severity
    ON tracking_loops (created_at DESC, status);

CREATE INDEX IF NOT EXISTS idx_features_window
    ON feature_windows (created_at DESC);

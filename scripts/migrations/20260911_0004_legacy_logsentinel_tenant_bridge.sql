-- Operator-approved bridge for the six known legacy identities.
-- This is migration-only adoption data; runtime authorization is generic.
BEGIN;

-- Published migration 0005 adds a NOT NULL column to the logs hypertable.
-- TimescaleDB rejects that operation while compression is enabled. Remove the
-- policy and decompress only this hypertable's chunks here; migration 0010
-- restores owner-aware compression after all ownership columns exist.
SELECT remove_compression_policy('logs', if_exists => TRUE);

DO $$
DECLARE
    chunk_row RECORD;
BEGIN
    FOR chunk_row IN
        SELECT chunk_schema, chunk_name
        FROM timescaledb_information.chunks
        WHERE hypertable_schema = 'public'
          AND hypertable_name = 'logs'
          AND is_compressed
    LOOP
        PERFORM decompress_chunk(
            format('%I.%I', chunk_row.chunk_schema, chunk_row.chunk_name)::regclass,
            if_compressed => TRUE
        );
    END LOOP;
END $$;

ALTER TABLE logs SET (timescaledb.compress = FALSE);

CREATE TABLE IF NOT EXISTS tenants (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_tenants_explicit_id CHECK (id <> 'default' AND length(trim(id)) > 0),
    CONSTRAINT ck_tenants_status CHECK (status IN ('active', 'suspended'))
);

CREATE TABLE IF NOT EXISTS tenant_memberships (
    tenant_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    role VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    PRIMARY KEY (tenant_id, user_id),
    CONSTRAINT ck_membership_role CHECK (role IN ('viewer', 'operator', 'admin')),
    CONSTRAINT ck_membership_status CHECK (status IN ('active', 'suspended'))
);

CREATE TABLE IF NOT EXISTS provider_tenant_mappings (
    provider VARCHAR(32) NOT NULL,
    issuer VARCHAR(512) NOT NULL,
    provider_tenant_id VARCHAR(128) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    default_role VARCHAR(32) NOT NULL DEFAULT 'viewer',
    PRIMARY KEY (provider, issuer, provider_tenant_id),
    CONSTRAINT ck_provider_mapping_role CHECK (default_role IN ('viewer', 'operator'))
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id VARCHAR(64) PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL,
    csrf_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    token_hash VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ NULL
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_provider_tenant_mapping_tenant') THEN
        ALTER TABLE provider_tenant_mappings ADD CONSTRAINT fk_provider_tenant_mapping_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_auth_sessions_tenant') THEN
        ALTER TABLE auth_sessions ADD CONSTRAINT fk_auth_sessions_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id);
    END IF;
END $$;

DO $$
DECLARE
    legacy_ids BIGINT[];
BEGIN
    SELECT COALESCE(array_agg(id ORDER BY id), ARRAY[]::BIGINT[])
      INTO legacy_ids FROM users WHERE tenant_id = 'default';

    -- A clean/current database has nothing to adopt. Any partially changed
    -- legacy set is unexpected and must stop before mutation.
    IF cardinality(legacy_ids) > 0
       AND legacy_ids <> ARRAY[5,6,7,8,12,14]::BIGINT[] THEN
        RAISE EXCEPTION 'legacy bridge baseline mismatch: refusing tenant adoption';
    END IF;

    IF cardinality(legacy_ids) > 0 AND (
        (SELECT COUNT(*) FROM users) <> 6 OR
        (SELECT COUNT(*) FROM external_identities) <> 6 OR
        EXISTS (
            SELECT 1 FROM external_identities e
            LEFT JOIN users u ON u.id = e.user_id
            WHERE u.id IS NULL
        )
    ) THEN
        RAISE EXCEPTION 'legacy identity baseline mismatch: refusing tenant adoption';
    END IF;
END $$;

INSERT INTO tenants (id, name, status)
SELECT 'logsentinel', 'LogSentinel', 'active'
WHERE EXISTS (SELECT 1 FROM users WHERE tenant_id = 'default')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;

UPDATE users
SET tenant_id = 'logsentinel',
    role = CASE WHEN id = 5 THEN 'admin' ELSE 'viewer' END,
    updated_at = NOW()
WHERE tenant_id = 'default' AND id = ANY (ARRAY[5,6,7,8,12,14]::BIGINT[]);

INSERT INTO tenant_memberships (tenant_id, user_id, role, status)
SELECT 'logsentinel', id, CASE WHEN id = 5 THEN 'admin' ELSE 'viewer' END, 'active'
FROM users WHERE id = ANY (ARRAY[5,6,7,8,12,14]::BIGINT[])
  AND tenant_id = 'logsentinel'
ON CONFLICT (tenant_id, user_id) DO UPDATE
SET role = EXCLUDED.role, status = EXCLUDED.status;

-- Preserve provider context and explicitly map each stored provider tuple to
-- the approved application tenant. No provider subject or user ID is used by
-- normal runtime authorization.
INSERT INTO provider_tenant_mappings
    (provider, issuer, provider_tenant_id, tenant_id, enabled, default_role)
SELECT DISTINCT lower(e.provider), e.issuer, COALESCE(e.tenant_id, ''),
       'logsentinel', TRUE, 'viewer'
FROM external_identities e
JOIN users u ON u.id = e.user_id
WHERE u.tenant_id = 'logsentinel'
ON CONFLICT (provider, issuer, provider_tenant_id) DO UPDATE
SET tenant_id = EXCLUDED.tenant_id, enabled = TRUE;

COMMIT;

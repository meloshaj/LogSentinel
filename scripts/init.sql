-- =============================================================================
-- LogSentinel — Physical Schema Initialization
-- Target: PostgreSQL 16+
-- Executed automatically via /docker-entrypoint-initdb.d/ on first container boot
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Enable TimescaleDB Extension
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- Database tuning is supplied by the local/demo container command or the
-- operator's PostgreSQL configuration.  Keeping ALTER SYSTEM out of the
-- schema bootstrap means a schema owner does not also need instance-level
-- configuration privileges, and a failed schema transaction cannot leave
-- unrelated postgresql.auto.conf changes behind.
-- ---------------------------------------------------------------------------

BEGIN;

-- ---------------------------------------------------------------------------
-- ENUM Types
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type AS t
        JOIN pg_namespace AS n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typname = 'severity_level'
    ) THEN
        CREATE TYPE severity_level AS ENUM (
            'INFO',
            'LOW',
            'MEDIUM',
            'HIGH',
            'CRITICAL'
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type AS t
        JOIN pg_namespace AS n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typname = 'incident_status'
    ) THEN
        CREATE TYPE incident_status AS ENUM (
            'OPEN',
            'INVESTIGATING',
            'MITIGATED',
            'RESOLVED'
        );
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Tenant authority and authentication lifecycle tables
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    id          VARCHAR(64) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    status      VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_tenants_explicit_id CHECK (id <> 'default' AND length(trim(id)) > 0),
    CONSTRAINT ck_tenants_status CHECK (status IN ('active', 'suspended'))
);

-- ---------------------------------------------------------------------------
-- Logs Table
-- Stores raw log events ingested from microservices via the async gateway.
-- template_id is populated by the Drain3 log parser for pattern clustering.
-- correlation_id enables distributed request tracing across services.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS logs (
    id              VARCHAR(26),
    event_id        VARCHAR(128)    NOT NULL,
    tenant_id       VARCHAR(64)     NOT NULL,
    owner_user_id   BIGINT          NOT NULL,
    timestamp       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    service         VARCHAR(255)    NOT NULL,
    raw_message     TEXT            NOT NULL,
    template_id     VARCHAR(64)     NOT NULL,
    template_text   TEXT            NULL,
    parameters      JSONB           NOT NULL DEFAULT '[]'::jsonb,
    level           VARCHAR(32)     NULL,
    source          VARCHAR(255)    NULL,
    environment     VARCHAR(255)    NULL,
    correlation_id  VARCHAR(128)    NULL,
    metadata        JSONB           NOT NULL DEFAULT '{}'::jsonb,
    parsed_at       TIMESTAMPTZ     NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    ingested_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, ingested_at, id)
);

-- Initialize TimescaleDB hypertable
SELECT create_hypertable('logs', 'ingested_at', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Continuous Aggregates (1-Minute Rollup)
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS logs_rollup_1m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', ingested_at) AS bucket,
    tenant_id,
    owner_user_id,
    service AS service_name,
    level AS log_level,
    COUNT(*) AS log_count,
    COUNT(*) FILTER (WHERE level IN ('ERROR', 'CRITICAL', 'FATAL'))::FLOAT / NULLIF(COUNT(*), 0) AS error_ratio
FROM logs
GROUP BY bucket, tenant_id, owner_user_id, service, level
WITH NO DATA;

SELECT add_continuous_aggregate_policy('logs_rollup_1m',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => true
);

-- ---------------------------------------------------------------------------
-- Data Retention Policy
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    PERFORM remove_retention_policy('logs', if_exists => true);
EXCEPTION WHEN OTHERS THEN
END $$;

-- SELECT add_retention_policy('logs', INTERVAL '30 days', if_not_exists => TRUE);

-- NOTE: Columns template_text, parameters, level, source, environment,
-- metadata, parsed_at, created_at are already defined in the CREATE TABLE
-- above. A previous ALTER TABLE ADD COLUMN block was removed because
-- TimescaleDB disallows ADD COLUMN with non-constant defaults (e.g. NOW())
-- on hypertables with columnstore (compression) enabled.

-- ---------------------------------------------------------------------------
-- Incidents Table
-- Tracks anomaly incidents detected by downstream AI/ML workers.
-- blast_radius uses FLOAT to represent fractional topology density scores.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS incidents (
    id              BIGSERIAL       PRIMARY KEY,
    tenant_id       VARCHAR(64)     NOT NULL REFERENCES tenants(id),
    owner_user_id   BIGINT          NOT NULL,
    root_cause      TEXT            NULL,
    severity        severity_level  NOT NULL,
    blast_radius    FLOAT           NULL,
    status          incident_status NOT NULL DEFAULT 'OPEN',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Feature Windows Table
-- Stores feature vectors extracted from sliding log windows by the
-- FeatureExtractionWorker.  Each row represents one window's worth of
-- computed features and optional anomaly scores.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_windows (
    id                  BIGSERIAL       PRIMARY KEY,
    tenant_id           VARCHAR(64)     NOT NULL,
    owner_user_id       BIGINT          NOT NULL,
    window_id           VARCHAR(128)    NOT NULL,
    start_time          TIMESTAMPTZ     NOT NULL,
    end_time            TIMESTAMPTZ     NOT NULL,
    service             VARCHAR(255)    NULL,
    log_count           INTEGER         NOT NULL DEFAULT 0,
    feature_vector      JSONB           NOT NULL DEFAULT '{}'::jsonb,
    anomaly_prediction  JSONB           NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_feature_windows_tenant_owner_window UNIQUE (tenant_id, owner_user_id, window_id)
);

-- ---------------------------------------------------------------------------
-- Anomaly Events Table
-- Individual anomaly detections linked to the feature window that produced
-- them.  Supports acknowledgement tracking for dashboard workflows.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS anomaly_events (
    id              BIGSERIAL       PRIMARY KEY,
    tenant_id       VARCHAR(64)     NOT NULL,
    owner_user_id   BIGINT          NOT NULL,
    window_id       VARCHAR(128)    NOT NULL,
    event_type      VARCHAR(64)     NOT NULL,
    severity        VARCHAR(32)     NOT NULL,
    score           FLOAT           NULL,
    details         JSONB           NULL,
    acknowledged    BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_anomaly_events_window FOREIGN KEY (tenant_id, owner_user_id, window_id)
        REFERENCES feature_windows(tenant_id, owner_user_id, window_id) ON DELETE CASCADE
    ,CONSTRAINT uq_anomaly_events_tenant_owner_window_type UNIQUE (tenant_id, owner_user_id, window_id, event_type)
);

-- ---------------------------------------------------------------------------
-- B-Tree Indexes
-- Optimized for asynchronous lookups by AI/ML background workers.
-- ---------------------------------------------------------------------------

-- Accelerates Drain3 template pattern grouping queries
CREATE INDEX IF NOT EXISTS idx_logs_template_id
    ON logs (tenant_id, owner_user_id, template_id);

-- Accelerates distributed trace correlation lookups
CREATE INDEX IF NOT EXISTS idx_logs_correlation_id
    ON logs (tenant_id, owner_user_id, correlation_id);

-- Accelerates time-range scans (essential for any log platform)
CREATE INDEX IF NOT EXISTS idx_logs_timestamp
    ON logs (tenant_id, owner_user_id, timestamp);

-- TimescaleDB compound indexes for high-frequency filtering
CREATE INDEX IF NOT EXISTS idx_logs_service_ingested_at
    ON logs (tenant_id, owner_user_id, service, ingested_at DESC);

CREATE INDEX IF NOT EXISTS idx_logs_level_ingested_at
    ON logs (tenant_id, owner_user_id, level, ingested_at DESC);

-- Accelerates open-incident dashboard queries
CREATE INDEX IF NOT EXISTS idx_incidents_status
    ON incidents (tenant_id, status);

-- Feature window lookups by window identifier
CREATE INDEX IF NOT EXISTS idx_feature_windows_window_id
    ON feature_windows (tenant_id, window_id);

-- Time-range scans over feature windows
CREATE INDEX IF NOT EXISTS idx_feature_windows_start_time
    ON feature_windows (tenant_id, start_time);

-- Anomaly event lookups by originating window
CREATE INDEX IF NOT EXISTS idx_anomaly_events_window_id
    ON anomaly_events (tenant_id, window_id);

-- Anomaly event recency / dashboard queries
CREATE INDEX IF NOT EXISTS idx_anomaly_events_created_at
    ON anomaly_events (tenant_id, created_at);

-- Anomaly severity filtering
CREATE INDEX IF NOT EXISTS idx_anomaly_events_severity
    ON anomaly_events (tenant_id, severity);

-- ---------------------------------------------------------------------------
-- Tracking Loops Table
-- Stores automated tracking-loop triggers emitted by EventManager.  The
-- nullable blast_radius payload is additive and remains absent for older rows.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tracking_loops (
    id              BIGSERIAL       PRIMARY KEY,
    tenant_id       VARCHAR(64)     NOT NULL,
    owner_user_id   BIGINT          NOT NULL,
    window_id       VARCHAR(128)    NOT NULL,
    anomaly_score   FLOAT           NOT NULL,
    status          VARCHAR(32)     NOT NULL DEFAULT 'ACTIVE',
    blast_radius    JSONB           NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_tracking_loops_window FOREIGN KEY (tenant_id, owner_user_id, window_id)
        REFERENCES feature_windows(tenant_id, owner_user_id, window_id) ON DELETE CASCADE,
    CONSTRAINT uq_tracking_loops_tenant_owner_window UNIQUE (tenant_id, owner_user_id, window_id)
);

ALTER TABLE tracking_loops
    ADD COLUMN IF NOT EXISTS blast_radius JSONB NULL;

CREATE INDEX IF NOT EXISTS idx_tracking_loops_window_id
    ON tracking_loops (tenant_id, window_id);

CREATE INDEX IF NOT EXISTS idx_tracking_loops_created_at
    ON tracking_loops (tenant_id, created_at);

-- ---------------------------------------------------------------------------
-- Users Table
-- Stores user registration records and hashed passwords.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL       PRIMARY KEY,
    email           VARCHAR(255)    NOT NULL UNIQUE,
    hashed_password VARCHAR(255)    NULL,
    full_name       VARCHAR(255)    NULL,
    organization    VARCHAR(255)    NULL,
    tenant_id       VARCHAR(64)     NOT NULL REFERENCES tenants(id),
    status          VARCHAR(32)     NOT NULL DEFAULT 'active',
    role            VARCHAR(32)     NOT NULL DEFAULT 'viewer',
    email_verified_at TIMESTAMPTZ   NULL,
    password_changed_at TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_users_status CHECK (status IN ('pending_verification', 'active', 'suspended')),
    CONSTRAINT ck_users_role CHECK (role IN ('viewer', 'operator', 'admin'))
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_logs_owner_user') THEN
        ALTER TABLE logs ADD CONSTRAINT fk_logs_owner_user FOREIGN KEY (owner_user_id) REFERENCES users(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_incidents_owner_user') THEN
        ALTER TABLE incidents ADD CONSTRAINT fk_incidents_owner_user FOREIGN KEY (owner_user_id) REFERENCES users(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_feature_windows_owner_user') THEN
        ALTER TABLE feature_windows ADD CONSTRAINT fk_feature_windows_owner_user FOREIGN KEY (owner_user_id) REFERENCES users(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_anomaly_events_owner_user') THEN
        ALTER TABLE anomaly_events ADD CONSTRAINT fk_anomaly_events_owner_user FOREIGN KEY (owner_user_id) REFERENCES users(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_tracking_loops_owner_user') THEN
        ALTER TABLE tracking_loops ADD CONSTRAINT fk_tracking_loops_owner_user FOREIGN KEY (owner_user_id) REFERENCES users(id);
    END IF;
END $$;

-- Enable compression only after all owner foreign keys and schema columns are
-- present; TimescaleDB blocks ALTER TABLE ownership DDL once enabled.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'logs' AND compression_enabled = true
    ) THEN
        ALTER TABLE logs SET (
            timescaledb.compress = true,
            timescaledb.compress_segmentby = 'tenant_id, owner_user_id, service',
            timescaledb.compress_orderby = 'timestamp DESC'
        );
    END IF;
END $$;
SELECT add_compression_policy('logs', INTERVAL '7 days', if_not_exists => TRUE);

-- Ensure hashed_password allows NULL for Google SSO users on pre-existing DB volumes
ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'viewer';
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ NULL;
UPDATE users SET email_verified_at = created_at WHERE status = 'active' AND email_verified_at IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_status'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT ck_users_status
            CHECK (status IN ('pending_verification', 'active', 'suspended'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_role'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT ck_users_role
            CHECK (role IN ('viewer', 'operator', 'admin'));
    END IF;
END $$;

-- Accelerates user lookups by email
CREATE INDEX IF NOT EXISTS idx_users_email
    ON users (email);

-- ---------------------------------------------------------------------------
-- Accounts and External Identities Tables
-- These tables are part of the active ORM/authentication contract.  They are
-- included in the canonical clean bootstrap so the application no longer
-- needs metadata.create_all to discover them at runtime.
-- ---------------------------------------------------------------------------

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

-- Tenant-scoped ingestion credentials. Plaintext keys are never stored.
CREATE TABLE IF NOT EXISTS ingestion_api_keys (
    id              BIGSERIAL       PRIMARY KEY,
    tenant_id       VARCHAR(64)     NOT NULL REFERENCES tenants(id),
    user_id         BIGINT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_prefix      VARCHAR(16)     NOT NULL,
    key_hash        VARCHAR(64)     NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ     NULL,
    expires_at      TIMESTAMPTZ     NULL,
    scopes          JSONB           NOT NULL DEFAULT '["logs:ingest"]'::jsonb,
    revoked_at      TIMESTAMPTZ     NULL
);

CREATE INDEX IF NOT EXISTS idx_ingestion_api_keys_tenant
    ON ingestion_api_keys (tenant_id, revoked_at);

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

CREATE TABLE IF NOT EXISTS tenant_memberships (
    tenant_id   VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        VARCHAR(32) NOT NULL,
    status      VARCHAR(32) NOT NULL DEFAULT 'active',
    PRIMARY KEY (tenant_id, user_id),
    CONSTRAINT ck_membership_role CHECK (role IN ('viewer', 'operator', 'admin')),
    CONSTRAINT ck_membership_status CHECK (status IN ('active', 'suspended'))
);

CREATE TABLE IF NOT EXISTS provider_tenant_mappings (
    provider            VARCHAR(32) NOT NULL,
    issuer              VARCHAR(512) NOT NULL,
    provider_tenant_id  VARCHAR(128) NOT NULL,
    tenant_id           VARCHAR(64) NOT NULL REFERENCES tenants(id),
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    default_role        VARCHAR(32) NOT NULL DEFAULT 'viewer',
    PRIMARY KEY (provider, issuer, provider_tenant_id),
    CONSTRAINT ck_provider_mapping_role CHECK (default_role IN ('viewer', 'operator'))
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id          VARCHAR(64) PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id   VARCHAR(64) NOT NULL REFERENCES tenants(id),
    csrf_hash   VARCHAR(64) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ NULL,
    revoked_at  TIMESTAMPTZ NULL,
    reuse_detected_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    token_hash  VARCHAR(64) PRIMARY KEY,
    session_id  VARCHAR(64) NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ NULL,
    consumed_at TIMESTAMPTZ NULL,
    rotated_from VARCHAR(64) NULL,
    rotated_to VARCHAR(64) NULL,
    revoked_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS email_outbox (
    id VARCHAR(64) PRIMARY KEY, user_id BIGINT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NULL REFERENCES tenants(id) ON DELETE CASCADE, kind VARCHAR(32) NOT NULL,
    recipient TEXT NOT NULL, template_data TEXT NOT NULL, idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), lease_expires_at TIMESTAMPTZ NULL,
    locked_by VARCHAR(128) NULL, last_error_category VARCHAR(128) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), delivered_at TIMESTAMPTZ NULL,
    CONSTRAINT ck_email_outbox_status CHECK (status IN ('pending','processing','retry','delivered','failed'))
);

-- PostgreSQL-authoritative password-reset capability lifecycle.  The raw
-- reset token is never stored here; only its SHA-256 digest is persisted.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_digest  VARCHAR(64) PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id     VARCHAR(64) NOT NULL REFERENCES tenants(id),
    issued_at     TIMESTAMPTZ NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    state         VARCHAR(16) NOT NULL DEFAULT 'issued',
    completed_at  TIMESTAMPTZ NULL,
    expired_at    TIMESTAMPTZ NULL,
    invalidated_at TIMESTAMPTZ NULL,
    CONSTRAINT ck_password_reset_tokens_state
        CHECK (state IN ('issued', 'completed', 'expired', 'invalidated')),
    CONSTRAINT ck_password_reset_tokens_expiry CHECK (expires_at > issued_at),
    CONSTRAINT ck_password_reset_tokens_completed_state CHECK (
        (state = 'completed' AND completed_at IS NOT NULL)
        OR (state <> 'completed' AND completed_at IS NULL)
    ),
    CONSTRAINT ck_password_reset_tokens_expired_state CHECK (
        (state = 'expired' AND expired_at IS NOT NULL)
        OR (state <> 'expired' AND expired_at IS NULL)
    ),
    CONSTRAINT ck_password_reset_tokens_invalidated_state CHECK (
        (state = 'invalidated' AND invalidated_at IS NOT NULL)
        OR (state <> 'invalidated' AND invalidated_at IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user_state
    ON password_reset_tokens (user_id, state, expires_at);

CREATE TABLE IF NOT EXISTS incident_triage_history (
    id BIGSERIAL PRIMARY KEY, tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tracking_loop_id BIGINT NOT NULL REFERENCES tracking_loops(id) ON DELETE CASCADE,
    actor_user_id BIGINT NOT NULL REFERENCES users(id), previous_status VARCHAR(32) NOT NULL,
    new_status VARCHAR(32) NOT NULL, note VARCHAR(1000) NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_settings (
    tenant_id VARCHAR(64) PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb, updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS model_artifacts (
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id), model_id VARCHAR(128) NOT NULL,
    owner_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version BIGINT NOT NULL, training_range TSTZRANGE NOT NULL,
    feature_schema_version VARCHAR(64) NOT NULL, artifact_uri VARCHAR(1024) NOT NULL,
    checksum VARCHAR(64) NOT NULL, training_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_at TIMESTAMPTZ NULL, PRIMARY KEY (tenant_id, owner_user_id, model_id, version),
    CONSTRAINT ck_model_artifact_status CHECK (status IN ('candidate','validated','active','previous','rejected'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_model_artifact ON model_artifacts (tenant_id, owner_user_id, model_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS security_audit_events (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   VARCHAR(64) NOT NULL REFERENCES tenants(id),
    user_id     BIGINT NOT NULL,
    action      VARCHAR(64) NOT NULL,
    resource_id VARCHAR(128) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Compound Indexes for High-Frequency Queries
-- ---------------------------------------------------------------------------

-- Accelerates GET /api/v1/logs/recent and paginated log queries
CREATE INDEX IF NOT EXISTS idx_logs_ingested_service
    ON logs (tenant_id, owner_user_id, ingested_at DESC, service);

-- Accelerates tracking-loop dashboard queries filtered by recency and status
CREATE INDEX IF NOT EXISTS idx_tracking_loops_severity
    ON tracking_loops (tenant_id, owner_user_id, created_at DESC, status);

-- Accelerates feature-window time-range scans used by the anomaly pipeline
CREATE INDEX IF NOT EXISTS idx_features_window
    ON feature_windows (tenant_id, owner_user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Schema lifecycle ledger
-- The bootstrap is version zero; forward migrations are recorded by
-- scripts/database_lifecycle.py.  This is metadata only and is not a
-- substitute for the application data model.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(128) PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    VARCHAR(64)  NULL,
    description TEXT         NOT NULL DEFAULT ''
);

-- Idempotency authority for at-least-once stream delivery. This remains an
-- ordinary table so uniqueness is not constrained by Timescale partitioning.
CREATE TABLE IF NOT EXISTS pipeline_ledger (
    tenant_id   VARCHAR(64) NOT NULL,
    owner_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stage       VARCHAR(32) NOT NULL,
    event_id    VARCHAR(128) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, owner_user_id, stage, event_id)
);

CREATE TABLE IF NOT EXISTS pipeline_outbox (
    id           VARCHAR(64) PRIMARY KEY,
    tenant_id    VARCHAR(64) NOT NULL,
    owner_user_id BIGINT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic        VARCHAR(32) NOT NULL,
    dedup_key    VARCHAR(128) NOT NULL,
    payload      JSONB NOT NULL,
    status       VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempts     INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at    TIMESTAMPTZ NULL,
    locked_by    VARCHAR(128) NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    delivery_type VARCHAR(32) NOT NULL DEFAULT 'generic',
    destination_id VARCHAR(128) NULL,
    event_id     VARCHAR(128) NULL,
    incident_id  VARCHAR(128) NULL,
    delivered_at TIMESTAMPTZ NULL,
    last_error_category VARCHAR(64) NULL,
    last_error   VARCHAR(128),
    UNIQUE (tenant_id, owner_user_id, topic, dedup_key),
    CONSTRAINT ck_pipeline_outbox_status CHECK (status IN ('pending', 'retry', 'processing', 'completed', 'delivered', 'failed', 'done', 'dead')),
    CONSTRAINT ck_pipeline_outbox_attempts CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS ix_pipeline_outbox_ready
    ON pipeline_outbox (topic, status, available_at);

-- Durable feature-stage source inputs. The outbox contribution row is
-- materialized here before it is marked completed, allowing a restarted
-- feature worker to rebuild only the active window horizon.
CREATE TABLE IF NOT EXISTS pipeline_feature_inputs (
    tenant_id       VARCHAR(64)  NOT NULL,
    owner_user_id   BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id        VARCHAR(128) NOT NULL,
    event_timestamp TIMESTAMPTZ  NOT NULL,
    payload         JSONB        NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, owner_user_id, event_id)
);

CREATE INDEX IF NOT EXISTS ix_pipeline_feature_inputs_timestamp
    ON pipeline_feature_inputs (tenant_id, owner_user_id, event_timestamp);

CREATE TABLE IF NOT EXISTS tenant_integrations (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    owner_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,
    destination_url TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_integrations_tenant_owner_provider UNIQUE (tenant_id, owner_user_id, provider),
    CONSTRAINT ck_tenant_integrations_provider CHECK (provider IN ('slack', 'discord'))
);

INSERT INTO schema_migrations (version, checksum, description)
VALUES ('0000_canonical_init', NULL, 'Canonical TimescaleDB bootstrap')
ON CONFLICT (version) DO NOTHING;

-- The additive reconciliation is included in this clean bootstrap. Existing
-- canonical volumes without this marker are advanced by the lifecycle runner.
INSERT INTO schema_migrations (version, checksum, description)
VALUES (
    '20260822_0001_schema_reconciliation',
    NULL,
    'Included in the canonical bootstrap; retained as the forward step for older canonical volumes.'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO schema_migrations (version, checksum, description)
VALUES (
    '20260826_0001_multitenant_partitioning',
    NULL,
    'Included in the canonical bootstrap; retained as the forward step for older canonical volumes.'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260826_0002_archive_manifest', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260831_0001_auth_lifecycle', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260907_0001_archive_rehydration_sessions', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260907_0002_ingestion_api_keys', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260907_0003_archive_security_and_model_registry', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260911_0004_legacy_logsentinel_tenant_bridge', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260911_0005_distributed_correctness', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260911_0006_durable_webhook_delivery', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260912_0007_application_release_gates', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260912_0008_tenant_authority', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260912_0009_pipeline_feature_durability', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260913_0010_per_user_data_ownership', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version, checksum, description) VALUES ('20260917_0011_password_reset_atomicity', NULL, 'Included in the canonical bootstrap') ON CONFLICT (version) DO NOTHING;

COMMIT;

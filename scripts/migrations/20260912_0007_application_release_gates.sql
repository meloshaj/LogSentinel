-- Session rotation, durable email, incident triage, settings, and model metadata.
BEGIN;

ALTER TABLE auth_sessions
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS reuse_detected_at TIMESTAMPTZ NULL;

ALTER TABLE auth_refresh_tokens
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS rotated_from VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS rotated_to VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS ix_auth_refresh_session
    ON auth_refresh_tokens (session_id, expires_at);

CREATE TABLE IF NOT EXISTS email_outbox (
    id VARCHAR(64) PRIMARY KEY,
    user_id BIGINT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind VARCHAR(32) NOT NULL,
    recipient TEXT NOT NULL,
    template_data TEXT NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_expires_at TIMESTAMPTZ NULL,
    locked_by VARCHAR(128) NULL,
    last_error_category VARCHAR(128) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMPTZ NULL,
    CONSTRAINT ck_email_outbox_status CHECK (status IN ('pending','processing','retry','delivered','failed')),
    CONSTRAINT ck_email_outbox_attempts CHECK (attempt_count >= 0)
);
CREATE INDEX IF NOT EXISTS ix_email_outbox_ready ON email_outbox (status, available_at);

CREATE TABLE IF NOT EXISTS incident_triage_history (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tracking_loop_id BIGINT NOT NULL REFERENCES tracking_loops(id) ON DELETE CASCADE,
    actor_user_id BIGINT NOT NULL REFERENCES users(id),
    previous_status VARCHAR(32) NOT NULL,
    new_status VARCHAR(32) NOT NULL,
    note VARCHAR(1000) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_incident_triage_status CHECK (new_status IN ('open','acknowledged','investigating','resolved'))
);
CREATE INDEX IF NOT EXISTS ix_incident_triage_history ON incident_triage_history (tenant_id, tracking_loop_id, created_at);

CREATE TABLE IF NOT EXISTS tenant_settings (
    tenant_id VARCHAR(64) PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE model_artifacts ADD COLUMN IF NOT EXISTS training_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;

BEGIN;

CREATE TABLE IF NOT EXISTS archive_rehydration_sessions (
    staging_table VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    archive_ids UUID[] NOT NULL DEFAULT '{}',
    request_key VARCHAR(128),
    expires_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL,
    rehydrated_rows BIGINT NOT NULL DEFAULT 0,
    status_reason VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_archive_rehydration_expiry
    ON archive_rehydration_sessions (expires_at);

COMMIT;

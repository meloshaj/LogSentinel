-- PostgreSQL-authoritative password-reset lifecycle for Remediation 07C.
-- Existing Valkey-only reset entries are deliberately not adopted: the new
-- endpoint never reads the legacy namespace, so pre-cutover capabilities are
-- safely invalidated when this migration is deployed.
BEGIN;

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

COMMIT;

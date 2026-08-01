-- =============================================================================
-- LogSentinel — Add External Identities Table
-- Supports federated authentication (Microsoft Entra ID, Google, etc.)
-- Must run AFTER the users table exists (see init.sql)
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- External Identities Table
-- Maps external provider identities to internal LogSentinel users.
-- The stable lookup key is (provider, issuer, subject).
-- Email is stored as informational profile data only, never as a lookup key.
-- ---------------------------------------------------------------------------

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

    -- Each (provider, issuer, subject) triple is globally unique
    CONSTRAINT uq_external_identities_provider_issuer_subject
        UNIQUE (provider, issuer, subject)
);

-- Accelerates lookups by user_id (e.g. "which providers is this user linked to?")
CREATE INDEX IF NOT EXISTS idx_external_identities_user_id
    ON external_identities (user_id);

-- Accelerates the primary identity lookup by (provider, issuer, subject)
-- The unique constraint already creates an implicit index, but this makes
-- the query plan explicit.
CREATE INDEX IF NOT EXISTS idx_external_identities_lookup
    ON external_identities (provider, issuer, subject);

COMMIT;

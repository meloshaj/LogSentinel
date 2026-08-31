-- =============================================================================
-- Migration: 20260831_0001_auth_lifecycle
-- Description: Add user lifecycle columns for email verification and
--              session invalidation via password_changed_at.
-- =============================================================================

BEGIN;

-- 0. Associate authenticated dashboard users with one authoritative tenant.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default';

-- 1. User status column — controls account lifecycle state machine
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active';

-- 2. Email verification timestamp — NULL until email is confirmed
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ NULL;

-- 3. Password change timestamp — used to invalidate JWTs issued before a
--    password reset by comparing against the token's iat claim
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ NULL;

-- 4. Backfill: existing users are retroactively considered active and verified
UPDATE users
SET status = 'active',
    email_verified_at = created_at
WHERE status = 'active'
  AND email_verified_at IS NULL;

-- Canonicalize legacy identities before enforcing case-insensitive lookup.
DO $$
BEGIN
    IF EXISTS (
        SELECT lower(trim(email))
        FROM users
        GROUP BY lower(trim(email))
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Cannot canonicalize users.email: duplicate case-insensitive identities exist';
    END IF;
END $$;

UPDATE users SET email = lower(trim(email));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_status'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT ck_users_status
            CHECK (status IN ('pending_verification', 'active', 'suspended'));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_canonical
    ON users (lower(email));

-- 5. Partial index for garbage-collecting abandoned pending registrations
CREATE INDEX IF NOT EXISTS idx_users_pending_cleanup
    ON users (status, created_at)
    WHERE status = 'pending_verification';

-- 6. Partial index for JWT invalidation lookups during get_current_user
CREATE INDEX IF NOT EXISTS idx_users_password_changed
    ON users (id, password_changed_at)
    WHERE password_changed_at IS NOT NULL;

COMMIT;

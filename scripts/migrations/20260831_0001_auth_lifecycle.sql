-- =============================================================================
-- Migration: 20260831_0001_auth_lifecycle
-- Description: Add user lifecycle columns for email verification and
--              session invalidation via password_changed_at.
-- =============================================================================

BEGIN;

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

-- 5. Partial index for garbage-collecting abandoned pending registrations
CREATE INDEX IF NOT EXISTS idx_users_pending_cleanup
    ON users (status, created_at)
    WHERE status = 'pending_verification';

-- 6. Partial index for JWT invalidation lookups during get_current_user
CREATE INDEX IF NOT EXISTS idx_users_password_changed
    ON users (id, password_changed_at)
    WHERE password_changed_at IS NOT NULL;

COMMIT;

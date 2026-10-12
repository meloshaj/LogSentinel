-- LogSentinel forward migration 20260912_0008
-- Tenant authority and API-key authorization metadata. Additive only.
-- Existing rows with NULL scopes intentionally remain unusable until an
-- operator performs an explicit, reviewed scope assignment.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.tenants') IS NULL
       OR to_regclass('public.users') IS NULL
       OR to_regclass('public.ingestion_api_keys') IS NULL
       OR to_regclass('public.tenant_memberships') IS NULL THEN
        RAISE EXCEPTION
            'tenant authority migration requires tenants, users, memberships, and ingestion_api_keys';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM ingestion_api_keys AS k
        LEFT JOIN tenants AS t ON t.id = k.tenant_id
        LEFT JOIN users AS u ON u.id = k.user_id
        WHERE t.id IS NULL OR u.id IS NULL
    ) THEN
        RAISE EXCEPTION
            'invalid ingestion_api_keys tenant/user references; refusing automatic repair';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM users AS u
        LEFT JOIN tenants AS t ON t.id = u.tenant_id
        WHERE t.id IS NULL
    ) THEN
        RAISE EXCEPTION
            'invalid users tenant references; refusing automatic repair';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tenant_memberships AS m
        LEFT JOIN tenants AS t ON t.id = m.tenant_id
        LEFT JOIN users AS u ON u.id = m.user_id
        WHERE t.id IS NULL OR u.id IS NULL
    ) THEN
        RAISE EXCEPTION
            'invalid tenant_memberships references; refusing automatic repair';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class child ON child.oid = c.conrelid
        JOIN pg_class parent ON parent.oid = c.confrelid
        WHERE c.contype = 'f'
          AND child.relname = 'users'
          AND parent.relname = 'tenants'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT fk_users_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class child ON child.oid = c.conrelid
        JOIN pg_class parent ON parent.oid = c.confrelid
        WHERE c.contype = 'f'
          AND child.relname = 'tenant_memberships'
          AND parent.relname = 'tenants'
    ) THEN
        ALTER TABLE tenant_memberships
            ADD CONSTRAINT fk_tenant_memberships_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class child ON child.oid = c.conrelid
        JOIN pg_class parent ON parent.oid = c.confrelid
        WHERE c.contype = 'f'
          AND child.relname = 'tenant_memberships'
          AND parent.relname = 'users'
    ) THEN
        ALTER TABLE tenant_memberships
            ADD CONSTRAINT fk_tenant_memberships_user
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;
END $$;

ALTER TABLE ingestion_api_keys
    ADD COLUMN IF NOT EXISTS scopes JSONB NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_ingestion_api_keys_tenant'
    ) THEN
        ALTER TABLE ingestion_api_keys
            ADD CONSTRAINT fk_ingestion_api_keys_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_ingestion_api_keys_scope_lookup
    ON ingestion_api_keys (tenant_id, revoked_at, expires_at);

COMMIT;

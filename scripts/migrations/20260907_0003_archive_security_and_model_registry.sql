-- Archive rehydration state, authorization, bounded cleanup, and model registry.
BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'viewer';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_role') THEN
        ALTER TABLE users ADD CONSTRAINT ck_users_role
            CHECK (role IN ('viewer', 'operator', 'admin'));
    END IF;
END $$;

ALTER TABLE archive_rehydration_sessions
    ADD COLUMN IF NOT EXISTS archive_ids UUID[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS request_key VARCHAR(128),
    ADD COLUMN IF NOT EXISTS status_reason VARCHAR(255),
    ADD COLUMN IF NOT EXISTS rehydrated_rows BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS cleanup_attempts INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS uq_archive_rehydration_tenant_request
    ON archive_rehydration_sessions (tenant_id, request_key)
    WHERE request_key IS NOT NULL;

ALTER TABLE archive_manifest
    ADD COLUMN IF NOT EXISTS object_size BIGINT,
    ADD COLUMN IF NOT EXISTS manifest_status VARCHAR(32) NOT NULL DEFAULT 'manifest_created',
    ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(255),
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS model_artifacts (
    tenant_id VARCHAR(64) NOT NULL,
    model_id VARCHAR(128) NOT NULL,
    version BIGINT NOT NULL,
    training_range TSTZRANGE NOT NULL,
    feature_schema_version VARCHAR(64) NOT NULL,
    artifact_uri VARCHAR(1024) NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_at TIMESTAMPTZ NULL,
    PRIMARY KEY (tenant_id, model_id, version),
    CONSTRAINT ck_model_artifact_status
      CHECK (status IN ('candidate', 'validated', 'active', 'previous', 'rejected'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_model_artifact
    ON model_artifacts (tenant_id, model_id)
    WHERE status = 'active';

COMMIT;

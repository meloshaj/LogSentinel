-- Remediation 02B: lease-aware durable webhook delivery and tenant destinations.
-- Additive only; this migration is not executed by repository changes.
BEGIN;

ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ NULL;
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS locked_by VARCHAR(128) NULL;
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL;
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS delivery_type VARCHAR(32) NOT NULL DEFAULT 'generic';
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS destination_id VARCHAR(128) NULL;
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS event_id VARCHAR(128) NULL;
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS incident_id VARCHAR(128) NULL;
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ NULL;
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS last_error_category VARCHAR(64) NULL;

ALTER TABLE pipeline_outbox DROP CONSTRAINT IF EXISTS ck_pipeline_outbox_status;
ALTER TABLE pipeline_outbox ADD CONSTRAINT ck_pipeline_outbox_status
    CHECK (status IN ('pending', 'retry', 'processing', 'delivered', 'failed', 'done', 'dead'));
CREATE INDEX IF NOT EXISTS ix_pipeline_outbox_lease
    ON pipeline_outbox (topic, status, lease_expires_at);

CREATE TABLE IF NOT EXISTS tenant_integrations (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,
    destination_url TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_integrations_tenant_provider UNIQUE (tenant_id, provider),
    CONSTRAINT ck_tenant_integrations_provider CHECK (provider IN ('slack', 'discord'))
);

COMMIT;

-- Remediation 02: additive idempotency and durable-work structures.
-- Safe for existing databases; no data is deleted and no production migration
-- is executed by this repository change.
BEGIN;

CREATE TABLE IF NOT EXISTS pipeline_ledger (
    tenant_id VARCHAR(64) NOT NULL,
    stage VARCHAR(32) NOT NULL,
    event_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, stage, event_id)
);

ALTER TABLE logs ADD COLUMN IF NOT EXISTS event_id VARCHAR(128);
UPDATE logs SET event_id = id WHERE event_id IS NULL;
ALTER TABLE logs ALTER COLUMN event_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_logs_event_id ON logs (tenant_id, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_anomaly_events_tenant_window_type
    ON anomaly_events (tenant_id, window_id, event_type);

CREATE TABLE IF NOT EXISTS pipeline_outbox (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    topic VARCHAR(32) NOT NULL,
    dedup_key VARCHAR(128) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error VARCHAR(128),
    UNIQUE (tenant_id, topic, dedup_key),
    CONSTRAINT ck_pipeline_outbox_status CHECK (status IN ('pending', 'done', 'dead')),
    CONSTRAINT ck_pipeline_outbox_attempts CHECK (attempts >= 0)
);
CREATE INDEX IF NOT EXISTS ix_pipeline_outbox_ready
    ON pipeline_outbox (topic, status, available_at);

COMMIT;

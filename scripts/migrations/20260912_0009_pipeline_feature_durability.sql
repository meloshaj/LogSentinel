-- Remediation 05B: durable feature contributions, windows, and incidents.
-- Additive only; this file is never applied by the application at runtime.
BEGIN;

ALTER TABLE pipeline_outbox DROP CONSTRAINT IF EXISTS ck_pipeline_outbox_status;
ALTER TABLE pipeline_outbox ADD CONSTRAINT ck_pipeline_outbox_status
    CHECK (status IN ('pending', 'retry', 'processing', 'completed', 'delivered', 'failed', 'done', 'dead'));

CREATE TABLE IF NOT EXISTS pipeline_feature_inputs (
    tenant_id       VARCHAR(64)  NOT NULL,
    event_id        VARCHAR(128) NOT NULL,
    event_timestamp TIMESTAMPTZ  NOT NULL,
    payload         JSONB        NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, event_id)
);

CREATE INDEX IF NOT EXISTS ix_pipeline_feature_inputs_timestamp
    ON pipeline_feature_inputs (tenant_id, event_timestamp);

-- A tracking loop is the durable incident identity for one tenant/window.
-- Existing duplicate data must be reviewed before this index is applied.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tracking_loops_tenant_window
    ON tracking_loops (tenant_id, window_id);

COMMIT;

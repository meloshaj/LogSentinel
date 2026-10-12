-- Per-user operational ownership and operator-approved synthetic reset.
-- Must run only after the legacy tenant bridge and published 0005-0009.
BEGIN;

LOCK TABLE logs, feature_windows, anomaly_events, tracking_loops IN ACCESS EXCLUSIVE MODE;

CREATE TABLE IF NOT EXISTS legacy_cleanup_audit (
    id BIGSERIAL PRIMARY KEY,
    cleanup_key VARCHAR(128) NOT NULL UNIQUE,
    legacy_logs_deleted BIGINT NOT NULL,
    feature_windows_deleted BIGINT NOT NULL,
    anomaly_events_deleted BIGINT NOT NULL,
    tracking_loops_deleted BIGINT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
DECLARE
    total_logs BIGINT;
    candidate_logs BIGINT;
    feature_count BIGINT;
    anomaly_count BIGINT;
    tracking_count BIGINT;
BEGIN
    SELECT COUNT(*) INTO total_logs FROM logs;
    SELECT COUNT(*) INTO candidate_logs
    FROM logs
    WHERE source = 'telemetry-generator'
      AND environment = 'stress-test'
      AND timestamp >= TIMESTAMPTZ '2026-08-31 00:00:00+00'
      AND timestamp <  TIMESTAMPTZ '2026-09-01 00:00:00+00'
      AND ingested_at >= TIMESTAMPTZ '2026-08-31 00:00:00+00'
      AND ingested_at <  TIMESTAMPTZ '2026-09-01 00:00:00+00';
    SELECT COUNT(*) INTO feature_count FROM feature_windows;
    SELECT COUNT(*) INTO anomaly_count FROM anomaly_events;
    SELECT COUNT(*) INTO tracking_count FROM tracking_loops;

    IF total_logs = 0 AND feature_count = 0 AND anomaly_count = 0 AND tracking_count = 0 THEN
        RETURN;
    END IF;
    IF total_logs <> 3153 OR candidate_logs <> 3153
       OR feature_count <> 48 OR anomaly_count <> 48 OR tracking_count <> 48 THEN
        RAISE EXCEPTION 'legacy cleanup baseline mismatch; refusing to delete operational data';
    END IF;
    IF EXISTS (SELECT 1 FROM feature_windows WHERE tenant_id <> 'default')
       OR EXISTS (SELECT 1 FROM anomaly_events WHERE tenant_id <> 'default')
       OR EXISTS (SELECT 1 FROM tracking_loops WHERE tenant_id <> 'default') THEN
        RAISE EXCEPTION 'legacy derived-data predicate mismatch; refusing cleanup';
    END IF;

    -- Actual FK order: anomaly_events/tracking_loops cascade from feature_windows.
    DELETE FROM feature_windows WHERE tenant_id = 'default';
    DELETE FROM logs
    WHERE source = 'telemetry-generator'
      AND environment = 'stress-test'
      AND timestamp >= TIMESTAMPTZ '2026-08-31 00:00:00+00'
      AND timestamp <  TIMESTAMPTZ '2026-09-01 00:00:00+00'
      AND ingested_at >= TIMESTAMPTZ '2026-08-31 00:00:00+00'
      AND ingested_at <  TIMESTAMPTZ '2026-09-01 00:00:00+00';

    IF EXISTS (SELECT 1 FROM logs) OR EXISTS (SELECT 1 FROM feature_windows)
       OR EXISTS (SELECT 1 FROM anomaly_events) OR EXISTS (SELECT 1 FROM tracking_loops) THEN
        RAISE EXCEPTION 'legacy cleanup postcondition failed';
    END IF;
    INSERT INTO legacy_cleanup_audit
        (cleanup_key, legacy_logs_deleted, feature_windows_deleted,
         anomaly_events_deleted, tracking_loops_deleted)
    VALUES ('2026-08-31-telemetry-generator-stress-test', 3153, 48, 48, 48)
    ON CONFLICT (cleanup_key) DO NOTHING;
END $$;

ALTER TABLE logs ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE logs ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE feature_windows ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE feature_windows ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE anomaly_events ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE anomaly_events ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE tracking_loops ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE tracking_loops ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE pipeline_ledger ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE pipeline_ledger ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS owner_user_id BIGINT NULL;
ALTER TABLE pipeline_outbox ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE pipeline_feature_inputs ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE pipeline_feature_inputs ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE archive_manifest ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE archive_manifest ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE archive_rehydration_sessions ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE model_artifacts ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
ALTER TABLE model_artifacts ADD COLUMN IF NOT EXISTS retention_deadline_at TIMESTAMPTZ NULL;
ALTER TABLE tenant_integrations ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;

-- After the reviewed legacy reset, no operational table may silently create
-- rows in the retired placeholder tenant.
ALTER TABLE logs ALTER COLUMN tenant_id DROP DEFAULT;
ALTER TABLE feature_windows ALTER COLUMN tenant_id DROP DEFAULT;
ALTER TABLE anomaly_events ALTER COLUMN tenant_id DROP DEFAULT;
ALTER TABLE tracking_loops ALTER COLUMN tenant_id DROP DEFAULT;
ALTER TABLE incidents ALTER COLUMN tenant_id DROP DEFAULT;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM logs WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM feature_windows WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM anomaly_events WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM tracking_loops WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM incidents WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM pipeline_ledger WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM pipeline_feature_inputs WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM archive_manifest WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM archive_rehydration_sessions WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM model_artifacts WHERE owner_user_id IS NULL)
       OR EXISTS (SELECT 1 FROM tenant_integrations WHERE owner_user_id IS NULL) THEN
        RAISE EXCEPTION 'unowned operational rows remain; refusing ownership constraints';
    END IF;
END $$;

ALTER TABLE logs ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE feature_windows ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE anomaly_events ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE tracking_loops ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE incidents ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE pipeline_ledger ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE pipeline_feature_inputs ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE archive_manifest ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE archive_rehydration_sessions ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE model_artifacts ALTER COLUMN owner_user_id SET NOT NULL;
ALTER TABLE tenant_integrations ALTER COLUMN owner_user_id SET NOT NULL;

-- Drop dependent legacy foreign keys before replacing their referenced key.
ALTER TABLE anomaly_events DROP CONSTRAINT IF EXISTS fk_anomaly_events_window;
ALTER TABLE tracking_loops DROP CONSTRAINT IF EXISTS fk_tracking_loops_window;
ALTER TABLE feature_windows DROP CONSTRAINT IF EXISTS uq_feature_windows_tenant_window;
ALTER TABLE feature_windows DROP CONSTRAINT IF EXISTS uq_feature_windows_tenant_window_id;
ALTER TABLE feature_windows DROP CONSTRAINT IF EXISTS uq_feature_windows_tenant_owner_window;
ALTER TABLE feature_windows ADD CONSTRAINT uq_feature_windows_tenant_owner_window
    UNIQUE (tenant_id, owner_user_id, window_id);
DROP INDEX IF EXISTS uq_anomaly_events_tenant_window_type;
ALTER TABLE anomaly_events DROP CONSTRAINT IF EXISTS uq_anomaly_events_tenant_owner_window_type;
ALTER TABLE anomaly_events DROP CONSTRAINT IF EXISTS fk_anomaly_events_owner_window;
ALTER TABLE anomaly_events ADD CONSTRAINT fk_anomaly_events_owner_window
    FOREIGN KEY (tenant_id, owner_user_id, window_id)
    REFERENCES feature_windows(tenant_id, owner_user_id, window_id) ON DELETE CASCADE;
ALTER TABLE anomaly_events ADD CONSTRAINT uq_anomaly_events_tenant_owner_window_type
    UNIQUE (tenant_id, owner_user_id, window_id, event_type);
ALTER TABLE tracking_loops DROP CONSTRAINT IF EXISTS uq_tracking_loops_tenant_window;
ALTER TABLE tracking_loops DROP CONSTRAINT IF EXISTS uq_tracking_loops_tenant_window_id;
ALTER TABLE tracking_loops DROP CONSTRAINT IF EXISTS uq_tracking_loops_tenant_owner_window;
ALTER TABLE tracking_loops DROP CONSTRAINT IF EXISTS fk_tracking_loops_owner_window;
ALTER TABLE tracking_loops ADD CONSTRAINT fk_tracking_loops_owner_window
    FOREIGN KEY (tenant_id, owner_user_id, window_id)
    REFERENCES feature_windows(tenant_id, owner_user_id, window_id) ON DELETE CASCADE;
ALTER TABLE tracking_loops ADD CONSTRAINT uq_tracking_loops_tenant_owner_window
    UNIQUE (tenant_id, owner_user_id, window_id);

ALTER TABLE pipeline_ledger DROP CONSTRAINT IF EXISTS pipeline_ledger_pkey;
ALTER TABLE pipeline_ledger ADD PRIMARY KEY (tenant_id, owner_user_id, stage, event_id);
ALTER TABLE pipeline_feature_inputs DROP CONSTRAINT IF EXISTS pipeline_feature_inputs_pkey;
ALTER TABLE pipeline_feature_inputs ADD PRIMARY KEY (tenant_id, owner_user_id, event_id);
ALTER TABLE pipeline_outbox DROP CONSTRAINT IF EXISTS pipeline_outbox_tenant_id_topic_dedup_key_key;
ALTER TABLE pipeline_outbox DROP CONSTRAINT IF EXISTS uq_pipeline_outbox_owner_dedup;
ALTER TABLE pipeline_outbox ADD CONSTRAINT uq_pipeline_outbox_owner_dedup
    UNIQUE (tenant_id, owner_user_id, topic, dedup_key);
ALTER TABLE tenant_integrations DROP CONSTRAINT IF EXISTS uq_tenant_integrations_tenant_provider;
ALTER TABLE tenant_integrations DROP CONSTRAINT IF EXISTS uq_tenant_integrations_tenant_owner_provider;
ALTER TABLE tenant_integrations ADD CONSTRAINT uq_tenant_integrations_tenant_owner_provider
    UNIQUE (tenant_id, owner_user_id, provider);
ALTER TABLE model_artifacts DROP CONSTRAINT IF EXISTS model_artifacts_pkey;
DROP INDEX IF EXISTS uq_active_model_artifact;
ALTER TABLE model_artifacts ADD PRIMARY KEY (tenant_id, owner_user_id, model_id, version);
CREATE UNIQUE INDEX uq_active_model_artifact
    ON model_artifacts (tenant_id, owner_user_id, model_id) WHERE status = 'active';

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'logs','feature_windows','anomaly_events','tracking_loops','incidents',
        'pipeline_ledger','pipeline_outbox','pipeline_feature_inputs',
        'archive_manifest','archive_rehydration_sessions','model_artifacts','tenant_integrations'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = table_name::regclass
              AND contype = 'f'
              AND confrelid = 'users'::regclass
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid=table_name::regclass AND attname='owner_user_id')]::SMALLINT[]
        ) THEN
            EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE',
                           table_name, 'fk_' || table_name || '_owner_user');
        END IF;
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_logs_owner_ingested
    ON logs (tenant_id, owner_user_id, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_owner_correlation
    ON logs (tenant_id, owner_user_id, correlation_id);
CREATE INDEX IF NOT EXISTS idx_feature_windows_owner_created
    ON feature_windows (tenant_id, owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_owner_created
    ON anomaly_events (tenant_id, owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tracking_loops_owner_created
    ON tracking_loops (tenant_id, owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_archive_manifest_owner_range
    ON archive_manifest (tenant_id, owner_user_id, dataset, range_start);

DROP MATERIALIZED VIEW IF EXISTS logs_rollup_1m;
CREATE MATERIALIZED VIEW logs_rollup_1m
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', ingested_at) AS bucket,
       tenant_id, owner_user_id, service AS service_name, level AS log_level,
       COUNT(*) AS log_count,
       COUNT(*) FILTER (WHERE level IN ('ERROR','CRITICAL','FATAL'))::FLOAT
           / NULLIF(COUNT(*), 0) AS error_ratio
FROM logs
GROUP BY bucket, tenant_id, owner_user_id, service, level
WITH NO DATA;
SELECT add_continuous_aggregate_policy('logs_rollup_1m',
    start_offset => INTERVAL '2 hours', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE);

-- An explicitly configured tenant setting named operational_retention_days
-- establishes the final deadline. NULL means the operator has not configured
-- final deletion; the 30-day archive threshold remains hot-to-cold only.
CREATE OR REPLACE FUNCTION set_operational_retention_deadline()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE configured_days INTEGER;
BEGIN
    SELECT NULLIF(settings->>'operational_retention_days', '')::INTEGER
      INTO configured_days FROM tenant_settings WHERE tenant_id = NEW.tenant_id;
    IF configured_days IS NOT NULL THEN
        IF configured_days <= 0 THEN
            RAISE EXCEPTION 'operational_retention_days must be positive';
        END IF;
        NEW.retention_deadline_at := COALESCE(NEW.retention_deadline_at, NEW.created_at + make_interval(days => configured_days));
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_logs_retention_deadline ON logs;
CREATE TRIGGER trg_logs_retention_deadline BEFORE INSERT ON logs
FOR EACH ROW EXECUTE FUNCTION set_operational_retention_deadline();
DROP TRIGGER IF EXISTS trg_feature_windows_retention_deadline ON feature_windows;
CREATE TRIGGER trg_feature_windows_retention_deadline BEFORE INSERT ON feature_windows
FOR EACH ROW EXECUTE FUNCTION set_operational_retention_deadline();
DROP TRIGGER IF EXISTS trg_incidents_retention_deadline ON incidents;
CREATE TRIGGER trg_incidents_retention_deadline BEFORE INSERT ON incidents
FOR EACH ROW EXECUTE FUNCTION set_operational_retention_deadline();
DROP TRIGGER IF EXISTS trg_pipeline_feature_inputs_retention_deadline ON pipeline_feature_inputs;
CREATE TRIGGER trg_pipeline_feature_inputs_retention_deadline BEFORE INSERT ON pipeline_feature_inputs
FOR EACH ROW EXECUTE FUNCTION set_operational_retention_deadline();
DROP TRIGGER IF EXISTS trg_pipeline_ledger_retention_deadline ON pipeline_ledger;
CREATE TRIGGER trg_pipeline_ledger_retention_deadline BEFORE INSERT ON pipeline_ledger
FOR EACH ROW EXECUTE FUNCTION set_operational_retention_deadline();
DROP TRIGGER IF EXISTS trg_pipeline_outbox_retention_deadline ON pipeline_outbox;
CREATE TRIGGER trg_pipeline_outbox_retention_deadline BEFORE INSERT ON pipeline_outbox
FOR EACH ROW EXECUTE FUNCTION set_operational_retention_deadline();
DROP TRIGGER IF EXISTS trg_model_artifacts_retention_deadline ON model_artifacts;
CREATE TRIGGER trg_model_artifacts_retention_deadline BEFORE INSERT ON model_artifacts
FOR EACH ROW EXECUTE FUNCTION set_operational_retention_deadline();

ALTER TABLE logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'tenant_id,owner_user_id',
    timescaledb.compress_orderby = 'ingested_at DESC'
);
SELECT add_compression_policy('logs', INTERVAL '7 days', if_not_exists => TRUE);

COMMIT;

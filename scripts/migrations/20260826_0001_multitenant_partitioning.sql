-- =============================================================================
-- Migration: 20260826_0001_multitenant_partitioning
-- Description: Implement authoritative multi-tenancy and switch hypertable
--              partitioning to ingestion time.
-- =============================================================================

BEGIN;

-- 1. Add tenant_id to supporting tables (excluding logs which will be recreated)
ALTER TABLE feature_windows ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE anomaly_events ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE tracking_loops ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default';

-- 2. Modify continuous aggregate
-- Timescale requires dropping the continuous aggregate policy and the materialized view before altering base tables.
DO $$
BEGIN
    PERFORM remove_continuous_aggregate_policy('logs_rollup_1m', if_exists => true);
EXCEPTION WHEN OTHERS THEN
END $$;
DROP MATERIALIZED VIEW IF EXISTS logs_rollup_1m;

-- 3. Rename old logs hypertable and its indexes
ALTER TABLE logs RENAME TO logs_old;
ALTER INDEX idx_logs_template_id RENAME TO idx_logs_template_id_old;
ALTER INDEX idx_logs_correlation_id RENAME TO idx_logs_correlation_id_old;
ALTER INDEX idx_logs_timestamp RENAME TO idx_logs_timestamp_old;
ALTER INDEX idx_logs_service_created_at RENAME TO idx_logs_service_created_at_old;
ALTER INDEX idx_logs_level_created_at RENAME TO idx_logs_level_created_at_old;
ALTER INDEX idx_logs_created_service RENAME TO idx_logs_created_service_old;

-- 4. Create new logs table with tenant_id and ingested_at
CREATE TABLE logs (
    id              VARCHAR(26),
    tenant_id       VARCHAR(64)     NOT NULL DEFAULT 'default',
    timestamp       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    service         VARCHAR(255)    NOT NULL,
    raw_message     TEXT            NOT NULL,
    template_id     VARCHAR(64)     NOT NULL,
    template_text   TEXT            NULL,
    parameters      JSONB           NOT NULL DEFAULT '[]'::jsonb,
    level           VARCHAR(32)     NULL,
    source          VARCHAR(255)    NULL,
    environment     VARCHAR(255)    NULL,
    correlation_id  VARCHAR(128)    NULL,
    metadata        JSONB           NOT NULL DEFAULT '{}'::jsonb,
    parsed_at       TIMESTAMPTZ     NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    ingested_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, ingested_at, id)
);

-- 5. Initialize TimescaleDB hypertable partitioned by ingested_at
SELECT create_hypertable('logs', 'ingested_at', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);

-- Configure Columnar Compression
ALTER TABLE logs SET (
    timescaledb.compress = true,
    timescaledb.compress_segmentby = 'tenant_id, service',
    timescaledb.compress_orderby = 'timestamp DESC'
);

-- Add Automated Policies (Compression > 7 days)
SELECT add_compression_policy('logs', INTERVAL '7 days', if_not_exists => TRUE);

-- 6. Copy data from old table to new table (for local/demo environments)
INSERT INTO logs (
    id, timestamp, service, raw_message, template_id, template_text, parameters, level, source, environment, correlation_id, metadata, parsed_at, created_at, ingested_at
)
SELECT 
    id, timestamp, service, raw_message, template_id, template_text, parameters, level, source, environment, correlation_id, metadata, parsed_at, created_at, created_at
FROM logs_old;

-- 7. Drop old table
DROP TABLE logs_old CASCADE;

-- 8. Recreate Continuous Aggregates (1-Minute Rollup) with tenant_id
CREATE MATERIALIZED VIEW logs_rollup_1m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', ingested_at) AS bucket,
    tenant_id,
    service AS service_name,
    level AS log_level,
    COUNT(*) AS log_count,
    COUNT(*) FILTER (WHERE level IN ('ERROR', 'CRITICAL', 'FATAL'))::FLOAT / NULLIF(COUNT(*), 0) AS error_ratio
FROM logs
GROUP BY bucket, tenant_id, service, level
WITH NO DATA;

SELECT add_continuous_aggregate_policy('logs_rollup_1m',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => true
);

-- 9. Recreate indexes for the new logs table with tenant_id priority
CREATE INDEX idx_logs_template_id ON logs (tenant_id, template_id);
CREATE INDEX idx_logs_correlation_id ON logs (tenant_id, correlation_id);
CREATE INDEX idx_logs_timestamp ON logs (tenant_id, timestamp);
CREATE INDEX idx_logs_service_ingested_at ON logs (tenant_id, service, ingested_at DESC);
CREATE INDEX idx_logs_level_ingested_at ON logs (tenant_id, level, ingested_at DESC);
CREATE INDEX idx_logs_ingested_service ON logs (tenant_id, ingested_at DESC, service);

-- Recreate feature_windows, anomaly_events, tracking_loops, incidents indexes with tenant_id (optional, but good practice if multitenancy is authoritative)
DROP INDEX IF EXISTS idx_feature_windows_window_id;
CREATE INDEX idx_feature_windows_window_id ON feature_windows (tenant_id, window_id);

DROP INDEX IF EXISTS idx_anomaly_events_window_id;
CREATE INDEX idx_anomaly_events_window_id ON anomaly_events (tenant_id, window_id);

DROP INDEX IF EXISTS idx_tracking_loops_window_id;
CREATE INDEX idx_tracking_loops_window_id ON tracking_loops (tenant_id, window_id);

COMMIT;

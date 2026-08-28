-- =============================================================================
-- Migration: TimescaleDB Continuous Aggregates and Updated Policies
-- =============================================================================

-- 1. Create a 1-minute Continuous Aggregate tracking service, level, log counts, and error ratios.
CREATE MATERIALIZED VIEW IF NOT EXISTS logs_rollup_1m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', created_at) AS bucket,
    service AS service_name,
    level AS log_level,
    COUNT(*) AS log_count,
    COUNT(*) FILTER (WHERE level IN ('ERROR', 'CRITICAL', 'FATAL'))::FLOAT / NULLIF(COUNT(*), 0) AS error_ratio
FROM logs
GROUP BY bucket, service, level
WITH NO DATA;

-- 2. Add automated continuous aggregate refresh policies running every 1 minute with a 2-hour rolling window.
SELECT add_continuous_aggregate_policy('logs_rollup_1m',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => true
);

-- 3. Configure hypertable chunk compression on logs older than 7 days
-- Remove the old policy first if we are migrating an existing database
SELECT remove_compression_policy('logs', if_exists => true);

ALTER TABLE logs SET (
    timescaledb.compress = true,
    timescaledb.compress_segmentby = 'service',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('logs', INTERVAL '7 days', if_not_exists => true);

-- 4. Add a data retention policy dropping compressed raw chunks older than 30 days.
-- First, if we had a custom safe_drop_chunks_job, we could remove it. We'll ensure the standard policy is used.
DO $$ 
DECLARE 
    job_id_val INT; 
BEGIN 
    SELECT job_id INTO job_id_val FROM timescaledb_information.jobs WHERE proc_name = 'safe_drop_chunks_job';
    IF job_id_val IS NOT NULL THEN 
        PERFORM delete_job(job_id_val); 
    END IF; 
END $$;

SELECT remove_retention_policy('logs', if_exists => true);
SELECT add_retention_policy('logs', INTERVAL '30 days', if_not_exists => true);

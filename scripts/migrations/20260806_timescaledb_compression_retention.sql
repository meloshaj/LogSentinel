-- =============================================================================
-- LogSentinel — TimescaleDB Compression and Retention
-- Target: PostgreSQL 16+ with TimescaleDB
-- =============================================================================

DO $$
BEGIN
    -- 1. Enable Columnar Compression
    -- Groups low-cardinality metadata (service, level) into blocks and orders by time/id.
    ALTER TABLE logs SET (
        timescaledb.compress = true,
        timescaledb.compress_segmentby = 'service, level',
        timescaledb.compress_orderby = 'created_at DESC, id DESC'
    );
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Compression configuration already set or failed: %', SQLERRM;
END $$;

-- 2. Add Automated Compression Policy (compress chunks > 2 days)
SELECT add_compression_policy('logs', INTERVAL '2 days', if_not_exists => TRUE);

-- 3. Add Automated Retention Policy (drop chunks > 30 days)
SELECT add_retention_policy('logs', INTERVAL '30 days', if_not_exists => TRUE);

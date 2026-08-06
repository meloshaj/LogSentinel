-- =============================================================================
-- LogSentinel — TimescaleDB Hypertable Migration
-- Target: PostgreSQL 16+ with TimescaleDB Extension
-- =============================================================================

-- ---------------------------------------------------------------------------
-- UP MIGRATION
-- Execute these statements sequentially.
-- NOTE: `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block, 
--       so DO NOT wrap this entire file in BEGIN/COMMIT.
-- ---------------------------------------------------------------------------

-- 1. Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 2. Drop existing single-column PK and replace with composite PK
DO $$ 
BEGIN
    -- Drop the primary key constraint if it exists
    IF EXISTS (
        SELECT 1 
        FROM information_schema.table_constraints 
        WHERE constraint_type = 'PRIMARY KEY' 
          AND table_name = 'logs'
    ) THEN
        ALTER TABLE logs DROP CONSTRAINT logs_pkey CASCADE;
    END IF;
    
    -- Add the new composite primary key
    -- Using an exception block to make it idempotent
    BEGIN
        ALTER TABLE logs ADD CONSTRAINT logs_pkey PRIMARY KEY (created_at, id);
    EXCEPTION WHEN duplicate_object OR duplicate_table THEN
        -- Do nothing if it already exists
    END;
END $$;

-- 3. Convert table to a hypertable partitioned by `created_at`
-- `migrate_data => true` handles pre-existing data in the table.
SELECT create_hypertable(
    'logs', 
    'created_at', 
    chunk_time_interval => INTERVAL '1 day', 
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- 4. Create compound indexes for high-frequency filtering
CREATE INDEX IF NOT EXISTS idx_logs_service_created_at
    ON logs (service, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_logs_level_created_at
    ON logs (level, created_at DESC);

-- 5. Drop redundant single-column index
DROP INDEX IF EXISTS idx_logs_created_at;

/*
-- ---------------------------------------------------------------------------
-- DOWN MIGRATION (Rollback)
-- Execute these if you need to revert the hypertable migration.
-- ---------------------------------------------------------------------------

-- 1. Recreate original index
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_logs_created_at ON logs (created_at);

-- 2. Drop the new compound indexes
DROP INDEX IF EXISTS idx_logs_service_created_at;
DROP INDEX IF EXISTS idx_logs_level_created_at;

-- 3. Un-hypertable (TimescaleDB doesn't have a simple revert for this once data is inserted,
--    so typically you have to create a new table, copy data over, and rename. 
--    However, if it's empty, you could drop the table, but logs is central.)
--    A data-preserving rollback requires a manual migration:
--      CREATE TABLE logs_old AS SELECT * FROM logs;
--      DROP TABLE logs;
--      ALTER TABLE logs_old RENAME TO logs;
--      ALTER TABLE logs ADD CONSTRAINT logs_pkey PRIMARY KEY (id);
--      -- Re-apply all other indexes / constraints.

*/

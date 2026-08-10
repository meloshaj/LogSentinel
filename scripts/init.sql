-- =============================================================================
-- LogSentinel — Physical Schema Initialization
-- Target: PostgreSQL 16+
-- Executed automatically via /docker-entrypoint-initdb.d/ on first container boot
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Enable TimescaleDB Extension
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- WAL Configuration Tuning for High Throughput
-- ---------------------------------------------------------------------------
ALTER SYSTEM SET max_wal_size = '16GB';
ALTER SYSTEM SET min_wal_size = '4GB';
ALTER SYSTEM SET checkpoint_timeout = '20min';
ALTER SYSTEM SET checkpoint_completion_target = '0.9';
ALTER SYSTEM SET wal_buffers = '64MB';
SELECT pg_reload_conf();

BEGIN;

-- ---------------------------------------------------------------------------
-- ENUM Types
-- ---------------------------------------------------------------------------
CREATE TYPE severity_level AS ENUM (
    'INFO',
    'LOW',
    'MEDIUM',
    'HIGH',
    'CRITICAL'
);

CREATE TYPE incident_status AS ENUM (
    'OPEN',
    'INVESTIGATING',
    'MITIGATED',
    'RESOLVED'
);

-- ---------------------------------------------------------------------------
-- Logs Table
-- Stores raw log events ingested from microservices via the async gateway.
-- template_id is populated by the Drain3 log parser for pattern clustering.
-- correlation_id enables distributed request tracing across services.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS logs (
    id              VARCHAR(26),
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
    PRIMARY KEY (created_at, id)
);

-- Initialize TimescaleDB hypertable
SELECT create_hypertable('logs', 'created_at', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);

-- Configure Columnar Compression
ALTER TABLE logs SET (
    timescaledb.compress = true,
    timescaledb.compress_segmentby = 'service, level',
    timescaledb.compress_orderby = 'created_at DESC, id DESC'
);

-- Add Automated Policies (Compression > 2 days)
SELECT add_compression_policy('logs', INTERVAL '2 days', if_not_exists => TRUE);

-- Lock-safe retention policy (Replaces add_retention_policy)
CREATE OR REPLACE PROCEDURE safe_drop_chunks(retention_interval INTERVAL, max_retries INT DEFAULT 5)
LANGUAGE plpgsql
AS $$
DECLARE
    retry_count INT := 0;
BEGIN
    WHILE retry_count < max_retries LOOP
        BEGIN
            SET LOCAL lock_timeout = '1000ms';
            PERFORM drop_chunks('logs', retention_interval);
            RAISE NOTICE 'Successfully dropped chunks older than %', retention_interval;
            RETURN;
        EXCEPTION WHEN lock_not_available THEN
            RAISE WARNING 'Could not acquire lock for drop_chunks (Attempt %/%). Retrying in 5 seconds...', retry_count + 1, max_retries;
            retry_count := retry_count + 1;
            PERFORM pg_sleep(5);
        END;
    END LOOP;
    RAISE WARNING 'Failed to drop chunks after % attempts due to lock contention. Deferring to next run.', max_retries;
END;
$$;

CREATE OR REPLACE PROCEDURE safe_drop_chunks_job(job_id INT, config JSONB)
LANGUAGE plpgsql
AS $$
DECLARE
    retention_interval INTERVAL;
    max_retries INT;
BEGIN
    retention_interval := COALESCE((config->>'retention_interval')::INTERVAL, INTERVAL '30 days');
    max_retries := COALESCE((config->>'max_retries')::INT, 5);
    CALL safe_drop_chunks(retention_interval, max_retries);
END;
$$;

DO $$
BEGIN
    PERFORM remove_retention_policy('logs', if_exists => true);
EXCEPTION WHEN OTHERS THEN
END $$;

SELECT add_job('safe_drop_chunks_job', '1 day', config => '{"retention_interval": "30 days", "max_retries": 5}');

ALTER TABLE logs
    ADD COLUMN IF NOT EXISTS template_text TEXT NULL,
    ADD COLUMN IF NOT EXISTS parameters JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS level VARCHAR(32) NULL,
    ADD COLUMN IF NOT EXISTS source VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS environment VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS parsed_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- ---------------------------------------------------------------------------
-- Incidents Table
-- Tracks anomaly incidents detected by downstream AI/ML workers.
-- blast_radius uses FLOAT to represent fractional topology density scores.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS incidents (
    id              BIGSERIAL       PRIMARY KEY,
    root_cause      TEXT            NULL,
    severity        severity_level  NOT NULL,
    blast_radius    FLOAT           NULL,
    status          incident_status NOT NULL DEFAULT 'OPEN',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Feature Windows Table
-- Stores feature vectors extracted from sliding log windows by the
-- FeatureExtractionWorker.  Each row represents one window's worth of
-- computed features and optional anomaly scores.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_windows (
    id                  BIGSERIAL       PRIMARY KEY,
    window_id           VARCHAR(128)    NOT NULL UNIQUE,
    start_time          TIMESTAMPTZ     NOT NULL,
    end_time            TIMESTAMPTZ     NOT NULL,
    service             VARCHAR(255)    NULL,
    log_count           INTEGER         NOT NULL DEFAULT 0,
    feature_vector      JSONB           NOT NULL DEFAULT '{}'::jsonb,
    anomaly_prediction  JSONB           NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Anomaly Events Table
-- Individual anomaly detections linked to the feature window that produced
-- them.  Supports acknowledgement tracking for dashboard workflows.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS anomaly_events (
    id              BIGSERIAL       PRIMARY KEY,
    window_id       VARCHAR(128)    NOT NULL
                        REFERENCES feature_windows(window_id) ON DELETE CASCADE,
    event_type      VARCHAR(64)     NOT NULL,
    severity        VARCHAR(32)     NOT NULL,
    score           FLOAT           NULL,
    details         JSONB           NULL,
    acknowledged    BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- B-Tree Indexes
-- Optimized for asynchronous lookups by AI/ML background workers.
-- ---------------------------------------------------------------------------

-- Accelerates Drain3 template pattern grouping queries
CREATE INDEX IF NOT EXISTS idx_logs_template_id
    ON logs (template_id);

-- Accelerates distributed trace correlation lookups
CREATE INDEX IF NOT EXISTS idx_logs_correlation_id
    ON logs (correlation_id);

-- Accelerates time-range scans (essential for any log platform)
CREATE INDEX IF NOT EXISTS idx_logs_timestamp
    ON logs (timestamp);

-- TimescaleDB compound indexes for high-frequency filtering
CREATE INDEX IF NOT EXISTS idx_logs_service_created_at
    ON logs (service, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_logs_level_created_at
    ON logs (level, created_at DESC);

-- Accelerates open-incident dashboard queries
CREATE INDEX IF NOT EXISTS idx_incidents_status
    ON incidents (status);

-- Feature window lookups by window identifier
CREATE INDEX IF NOT EXISTS idx_feature_windows_window_id
    ON feature_windows (window_id);

-- Time-range scans over feature windows
CREATE INDEX IF NOT EXISTS idx_feature_windows_start_time
    ON feature_windows (start_time);

-- Anomaly event lookups by originating window
CREATE INDEX IF NOT EXISTS idx_anomaly_events_window_id
    ON anomaly_events (window_id);

-- Anomaly event recency / dashboard queries
CREATE INDEX IF NOT EXISTS idx_anomaly_events_created_at
    ON anomaly_events (created_at);

-- Anomaly severity filtering
CREATE INDEX IF NOT EXISTS idx_anomaly_events_severity
    ON anomaly_events (severity);

-- ---------------------------------------------------------------------------
-- Tracking Loops Table
-- Stores automated tracking-loop triggers emitted by EventManager.  The
-- nullable blast_radius payload is additive and remains absent for older rows.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tracking_loops (
    id              BIGSERIAL       PRIMARY KEY,
    window_id       VARCHAR(128)    NOT NULL
                        REFERENCES feature_windows(window_id) ON DELETE CASCADE,
    anomaly_score   FLOAT           NOT NULL,
    status          VARCHAR(32)     NOT NULL DEFAULT 'triggered',
    blast_radius    JSONB           NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

ALTER TABLE tracking_loops
    ADD COLUMN IF NOT EXISTS blast_radius JSONB NULL;

CREATE INDEX IF NOT EXISTS idx_tracking_loops_window_id
    ON tracking_loops (window_id);

CREATE INDEX IF NOT EXISTS idx_tracking_loops_created_at
    ON tracking_loops (created_at);

-- ---------------------------------------------------------------------------
-- Users Table
-- Stores user registration records and hashed passwords.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL       PRIMARY KEY,
    email           VARCHAR(255)    NOT NULL UNIQUE,
    hashed_password VARCHAR(255)    NULL,
    full_name       VARCHAR(255)    NULL,
    organization    VARCHAR(255)    NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Ensure hashed_password allows NULL for Google SSO users on pre-existing DB volumes
ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL;

-- Accelerates user lookups by email
CREATE INDEX IF NOT EXISTS idx_users_email
    ON users (email);

-- ---------------------------------------------------------------------------
-- Compound Indexes for High-Frequency Queries
-- ---------------------------------------------------------------------------

-- Accelerates GET /api/v1/logs/recent and paginated log queries
CREATE INDEX IF NOT EXISTS idx_logs_created_service
    ON logs (created_at DESC, service);

-- Accelerates tracking-loop dashboard queries filtered by recency and status
CREATE INDEX IF NOT EXISTS idx_tracking_loops_severity
    ON tracking_loops (created_at DESC, status);

-- Accelerates feature-window time-range scans used by the anomaly pipeline
CREATE INDEX IF NOT EXISTS idx_features_window
    ON feature_windows (created_at DESC);

COMMIT;

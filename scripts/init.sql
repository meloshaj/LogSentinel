-- =============================================================================
-- LogSentinel — Physical Schema Initialization
-- Target: PostgreSQL 16+
-- Executed automatically via /docker-entrypoint-initdb.d/ on first container boot
-- =============================================================================

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
    id              BIGSERIAL       PRIMARY KEY,
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
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

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

-- Accelerates parsed-log insertion and dashboard recency checks
CREATE INDEX IF NOT EXISTS idx_logs_created_at
    ON logs (created_at);

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

COMMIT;

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
    correlation_id  VARCHAR(128)    NULL
);

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

-- Accelerates open-incident dashboard queries
CREATE INDEX IF NOT EXISTS idx_incidents_status
    ON incidents (status);

COMMIT;

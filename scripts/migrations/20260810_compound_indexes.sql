-- =============================================================================
-- Migration: 20260810 — Compound Indexes for High-Frequency Queries
-- Target: PostgreSQL 16+ with TimescaleDB
-- =============================================================================

-- Accelerates GET /api/v1/logs/recent and paginated log queries
-- that order by created_at DESC with optional service filtering.
CREATE INDEX IF NOT EXISTS idx_logs_created_service
    ON logs (created_at DESC, service);

-- Accelerates tracking-loop dashboard queries filtered by recency and status.
-- Note: the tracking_loops table uses 'status' (VARCHAR), not 'severity'.
CREATE INDEX IF NOT EXISTS idx_tracking_loops_severity
    ON tracking_loops (created_at DESC, status);

-- Accelerates feature-window time-range scans used by the anomaly pipeline.
CREATE INDEX IF NOT EXISTS idx_features_window
    ON feature_windows (created_at DESC);

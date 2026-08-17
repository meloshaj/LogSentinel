-- LogSentinel TimescaleDB Retention Policies

-- Add a 7-day retention policy for the raw logs hypertable
-- Logs older than 7 days will be automatically dropped.
SELECT add_retention_policy('logs', INTERVAL '7 days', if_not_exists => TRUE);

-- Add a 14-day retention policy for the feature windows hypertable
-- Feature windows older than 14 days will be automatically dropped.
SELECT add_retention_policy('feature_windows', INTERVAL '14 days', if_not_exists => TRUE);

-- Note: anomaly_events and tracking_loops are managed via custom cleanup scripts
-- since they may not be native hypertables or have specific business rules.

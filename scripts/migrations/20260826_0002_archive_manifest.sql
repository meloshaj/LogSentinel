-- =============================================================================
-- Migration: 20260826_0002_archive_manifest
-- Description: Create archive_manifest table for Cold Storage architecture
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS archive_manifest (
    archive_id              UUID            PRIMARY KEY,
    tenant_id               VARCHAR(64)     NOT NULL,
    dataset                 VARCHAR(64)     NOT NULL DEFAULT 'raw_logs',
    range_start             TIMESTAMPTZ     NOT NULL,
    range_end               TIMESTAMPTZ     NOT NULL,
    source_chunk_ids        TEXT[]          NOT NULL,
    schema_version          INT             NOT NULL,
    archive_format_version  INT             NOT NULL DEFAULT 1,
    generation              INT             NOT NULL DEFAULT 1,
    idempotency_key         VARCHAR(128)    UNIQUE NOT NULL,
    object_key              VARCHAR(512)    NOT NULL,
    sidecar_key             VARCHAR(512)    NOT NULL,
    format                  VARCHAR(32)     NOT NULL DEFAULT 'parquet',
    compression             VARCHAR(32)     NOT NULL DEFAULT 'zstd',
    row_count               BIGINT          NULL,
    min_ingested_at         TIMESTAMPTZ     NULL,
    max_ingested_at         TIMESTAMPTZ     NULL,
    sha256                  VARCHAR(64)     NULL,
    uncompressed_bytes      BIGINT          NULL,
    compressed_bytes        BIGINT          NULL,
    source_fingerprint      VARCHAR(64)     NULL,
    status                  VARCHAR(32)     NOT NULL, -- 'HOT', 'EXPORTING', 'STORED', 'VERIFIED', 'HOT_DELETED', 'CORRUPT'
    lease_owner             VARCHAR(128)    NULL,
    lease_expires_at        TIMESTAMPTZ     NULL,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    verified_at             TIMESTAMPTZ     NULL,
    deleted_from_hot_at     TIMESTAMPTZ     NULL,
    completed_at            TIMESTAMPTZ     NULL
);

CREATE INDEX idx_archive_manifest_status ON archive_manifest (status);
CREATE INDEX idx_archive_manifest_tenant ON archive_manifest (tenant_id, dataset, range_start);

COMMIT;

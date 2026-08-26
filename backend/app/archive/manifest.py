"""Sidecar metadata writer for Archive."""

import json
from datetime import datetime, timezone


def generate_sidecar_manifest(manifest_record: dict) -> bytes:
    """Generates the sidecar .manifest.json byte representation.

    Extracts relevant fields from the archive_manifest row dictionary and formats them.
    """

    def _format_dt(dt):
        if not dt:
            return None
        if isinstance(dt, datetime):
            return dt.isoformat()
        return dt

    sidecar = {
        "archive_id": str(manifest_record["archive_id"]),
        "tenant_id": manifest_record["tenant_id"],
        "dataset": manifest_record.get("dataset", "raw_logs"),
        "range_start": _format_dt(manifest_record["range_start"]),
        "range_end": _format_dt(manifest_record["range_end"]),
        "source_chunk_ids": manifest_record["source_chunk_ids"],
        "schema_version": manifest_record["schema_version"],
        "archive_format_version": manifest_record.get("archive_format_version", 1),
        "idempotency_key": manifest_record["idempotency_key"],
        "object_key": manifest_record["object_key"],
        "format": manifest_record.get("format", "parquet"),
        "compression": manifest_record.get("compression", "zstd"),
        "row_count": manifest_record.get("row_count"),
        "min_ingested_at": _format_dt(manifest_record.get("min_ingested_at")),
        "max_ingested_at": _format_dt(manifest_record.get("max_ingested_at")),
        "sha256": manifest_record.get("sha256"),
        "uncompressed_bytes": manifest_record.get("uncompressed_bytes"),
        "compressed_bytes": manifest_record.get("compressed_bytes"),
        "source_fingerprint": manifest_record.get("source_fingerprint"),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    return json.dumps(sidecar, indent=2).encode("utf-8")

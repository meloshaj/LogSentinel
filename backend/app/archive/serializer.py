"""Serializer for Hot/Cold Storage Architecture."""

import hashlib
import json
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

# Schema for Parquet serialization
ARCHIVE_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("tenant_id", pa.string()),
        ("timestamp", pa.timestamp("us", tz="UTC")),
        ("service", pa.string()),
        ("raw_message", pa.string()),
        ("template_id", pa.string()),
        ("template_text", pa.string()),
        ("parameters", pa.string()),  # JSON stringified
        ("level", pa.string()),
        ("source", pa.string()),
        ("environment", pa.string()),
        ("correlation_id", pa.string()),
        ("metadata", pa.string()),  # JSON stringified
        ("parsed_at", pa.timestamp("us", tz="UTC")),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


def serialize_to_parquet(rows: list[dict[str, Any]]) -> tuple[bytes, dict[str, Any]]:
    """Serializes a list of database rows into a Parquet byte buffer with ZSTD compression.

    Returns a tuple of (parquet_bytes, metadata_stats)
    """
    if not rows:
        return b"", {"row_count": 0}

    # Prepare columnar data
    columns = {field.name: [] for field in ARCHIVE_SCHEMA}  # type: ignore

    min_ingested_at = None
    max_ingested_at = None

    for row in rows:
        for field in ARCHIVE_SCHEMA:
            val = row.get(field.name)
            if field.name in ("parameters", "metadata") and isinstance(val, dict):
                val = json.dumps(val)
            columns[field.name].append(val)

        ingested_at = row.get("ingested_at")
        if ingested_at:
            if min_ingested_at is None or ingested_at < min_ingested_at:
                min_ingested_at = ingested_at
            if max_ingested_at is None or ingested_at > max_ingested_at:
                max_ingested_at = ingested_at

    arrays = [
        pa.array(columns[field.name], type=field.type) for field in ARCHIVE_SCHEMA
    ]
    table = pa.Table.from_arrays(arrays, schema=ARCHIVE_SCHEMA)

    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="ZSTD")
    buf = sink.getvalue()
    raw_bytes = buf.to_pybytes()

    # Calculate sha256
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

    stats = {
        "row_count": len(rows),
        "min_ingested_at": min_ingested_at,
        "max_ingested_at": max_ingested_at,
        "compressed_bytes": len(raw_bytes),
        "sha256": sha256_hash,
    }
    return raw_bytes, stats


import asyncio


async def async_serialize_to_parquet(
    rows: list[dict[str, Any]],
) -> tuple[bytes, dict[str, Any]]:
    """Asynchronous wrapper for serialize_to_parquet using asyncio.to_thread."""
    return await asyncio.to_thread(serialize_to_parquet, rows)

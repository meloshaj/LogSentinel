"""Canonical constants shared across LogSentinel workers and services.

Centralises magic strings so that consumers (DrainWorker, StreamCleanerWorker,
etc.) always reference the same Valkey stream and consumer group names.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Valkey stream identifiers
# ---------------------------------------------------------------------------
LOG_STREAM_NAME: str = (
    os.getenv("LOG_STREAM_NAME", "logs:stream").strip() or "logs:stream"
)
"""Name of the primary Valkey stream used for log ingestion."""

LOG_WORKERS_GROUP: str = "log_workers"
"""Consumer group shared by DrainWorker and StreamCleanerWorker."""

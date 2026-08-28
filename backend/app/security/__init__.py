"""Security dependencies for LogSentinel."""

from .ingest_guard import require_ingestion_api_key

__all__ = ["require_ingestion_api_key"]

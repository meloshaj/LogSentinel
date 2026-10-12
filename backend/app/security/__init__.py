"""Security dependencies for LogSentinel."""

__all__ = ["TenantContext", "get_tenant_context", "require_ingestion_api_key"]


def __getattr__(name: str):
    """Load security dependencies lazily to keep infrastructure imports acyclic."""
    if name == "require_ingestion_api_key":
        from .ingest_guard import require_ingestion_api_key

        return require_ingestion_api_key
    if name in {"TenantContext", "get_tenant_context"}:
        from .tenant_context import TenantContext, get_tenant_context

        return {
            "TenantContext": TenantContext,
            "get_tenant_context": get_tenant_context,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

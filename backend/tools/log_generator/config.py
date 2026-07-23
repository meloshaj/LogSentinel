"""Configuration schemas and default service topology for the mock log generator.

Defines ``ServiceNode`` (individual microservice profile) and
``GeneratorConfig`` (global generator settings).  A factory function
provides a realistic 5-service e-commerce topology out of the box.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Service-level configuration
# ---------------------------------------------------------------------------


class ServiceNode(BaseModel):
    """Profile for a single microservice in the simulated topology.

    ``service_type`` is a semantic tag consumed by the template library to
    pick domain-appropriate log messages (e.g. ``"gateway"`` → HTTP access
    logs, ``"database"`` → SQL execution logs).
    """

    service_name: str = Field(
        ...,
        min_length=1,
        description="Unique identifier for this service (e.g. 'api-gateway').",
    )
    service_type: str = Field(
        default="generic",
        min_length=1,
        description="Semantic type used to select log templates "
        "(gateway, auth, order, payment, database).",
    )
    error_rate: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Probability [0, 1] that a generated log event is an error.",
    )
    warn_rate: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Probability [0, 1] that a generated log event is a warning.",
    )
    base_latency_ms: float = Field(
        default=50.0,
        ge=0.0,
        description="Baseline request-processing latency in milliseconds.",
    )
    latency_jitter_ms: float = Field(
        default=25.0,
        ge=0.0,
        description="Random ± jitter added to base_latency_ms.",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="List of downstream service names this service calls.",
    )


# ---------------------------------------------------------------------------
# Generator-level configuration
# ---------------------------------------------------------------------------


class GeneratorConfig(BaseModel):
    """Global settings for the mock log generator."""

    target_url: str = Field(
        default="http://localhost:8000/ingest-log",
        description="Full URL of the LogSentinel ingestion endpoint.",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Ingestion API key sent via X-API-Key header.",
    )
    batch_size: int = Field(
        default=50,
        gt=0,
        description="Number of LogEntry objects per IngestPayload batch.",
    )
    logs_per_second: float = Field(
        default=100.0,
        gt=0.0,
        description="Target sustained throughput for the generator.",
    )
    environment: str = Field(
        default="development",
        min_length=1,
        description="Value written to IngestPayload.environment.",
    )
    default_source: str = Field(
        default="mock-log-generator",
        min_length=1,
        description="Value written to IngestPayload.source.",
    )
    services: list[ServiceNode] = Field(
        default_factory=list,
        description="Microservice topology nodes.",
    )
    trace_probability: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Probability that a generated batch includes a full "
        "distributed-trace sequence with a shared correlation_id.",
    )


# ---------------------------------------------------------------------------
# Default topology factory
# ---------------------------------------------------------------------------


def default_ecommerce_topology() -> GeneratorConfig:
    """Return a ready-to-use 5-service e-commerce topology.

    Dependency graph::

        api-gateway ──► auth-service
              │
              └──► order-service ──► payment-gateway
                        │
                        └──► inventory-db
    """
    services = [
        ServiceNode(
            service_name="api-gateway",
            service_type="gateway",
            error_rate=0.03,
            warn_rate=0.08,
            base_latency_ms=15.0,
            latency_jitter_ms=10.0,
            dependencies=["auth-service", "order-service"],
        ),
        ServiceNode(
            service_name="auth-service",
            service_type="auth",
            error_rate=0.04,
            warn_rate=0.12,
            base_latency_ms=25.0,
            latency_jitter_ms=15.0,
            dependencies=[],
        ),
        ServiceNode(
            service_name="order-service",
            service_type="order",
            error_rate=0.06,
            warn_rate=0.10,
            base_latency_ms=40.0,
            latency_jitter_ms=20.0,
            dependencies=["payment-gateway", "inventory-db"],
        ),
        ServiceNode(
            service_name="payment-gateway",
            service_type="payment",
            error_rate=0.08,
            warn_rate=0.15,
            base_latency_ms=120.0,
            latency_jitter_ms=60.0,
            dependencies=[],
        ),
        ServiceNode(
            service_name="inventory-db",
            service_type="database",
            error_rate=0.02,
            warn_rate=0.05,
            base_latency_ms=8.0,
            latency_jitter_ms=5.0,
            dependencies=[],
        ),
    ]

    return GeneratorConfig(services=services)

import asyncio
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import REGISTRY, Counter, Gauge
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field, ValidationError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .archive.rehydration import router as archive_rehydration_router
from .archive.worker import ArchiveWorker
from .core import (
    dispose_engine,
    get_database_settings,
    get_engine,
    init_engine,
    verify_connectivity,
    verify_schema_ready,
)
from .core.constants import LOG_STREAM_NAME, LOG_WORKERS_GROUP
from .core.database import get_async_session
from .core.ingest_limits import (
    MAX_COMPRESSED_BODY_BYTES,
    MAX_HTTP_BODY_BYTES,
    MAX_OTLP_BODY_BYTES,
    MAX_RECORDS_PER_BATCH,
    read_limited_body,
)
from .core.orm import UserRecord
from .core.rate_limit import limiter
from .core.redis import close_redis_pool, init_redis_pool
from .core.settings import (
    get_benchmarking_settings,
    get_drain3_pipeline_settings,
    get_graph_scoring_settings,
)
from .ml.anomaly_detector import (
    IsolationForestAnomalyDetector,
    get_canonical_model_path,
)
from .ml.feature_extractor import WindowConfig
from .observability.metrics import (
    observe_benchmarking_snapshot,
    observe_worker_stats,
    record_drain_worker_stats,
    record_feature_worker_stats,
    refresh_durable_operations,
    refresh_stream_metrics,
    set_ml_status,
)
from .repositories.feature_repository import FeatureRepository
from .repositories.log_repository import LogRepository
from .repositories.tracking_repository import TrackingRepository
from .routers.auth_router import router as auth_router
from .routers.benchmark_router import router as benchmark_router
from .routers.ingest import router as ingest_router
from .routers.ingest_bulk import router as ingest_bulk_router
from .routers.otel_receiver import router as otel_router
from .routers.product_state import router as product_state_router
from .schemas.blast_radius import BlastRadiusResult
from .schemas.graph_api import BlastRadiusRetrievalResponse, TopologyResponse
from .security.auth import (
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_SECRET_KEY,
    authenticate_token,
)
from .security.tenant_context import (
    TenantContext,
    get_tenant_context,
    require_permission,
)
from .security.redaction import sanitize_error_text
from .security.tenants import resolve_membership
from .services.auth_cache import AuthCacheUnavailableError
from .services.batch_manager import ParsedLogBatchManager
from .services.benchmarking import BenchmarkingCollector
from .services.drain_parser import DrainParser
from .services.email_outbox import EmailDeliveryWorker
from .services.graph_analysis_service import GraphAnalysisService
from .services.runtime_dependency_parser import RuntimeDependencyParser
from .services.telemetry import telemetry_event, telemetry_manager
from .services.topology_pipeline import NetworkXTopologyPipeline
from .services.webhook_delivery import WebhookDeliveryWorker
from .workers.drain_worker import DrainWorker
from .workers.event_manager import EventManager
from .workers.feature_worker import FeatureExtractionWorker
from .workers.stream_cleaner import StreamCleanerWorker


def _get_or_create_metric(
    metric_type: type[Counter] | type[Gauge],
    name: str,
    documentation: str,
    labelnames: list[str] | tuple[str, ...] = (),
) -> Counter | Gauge:
    """Reuse an identically-defined process metric across module reloads.

    Prometheus' Counter collector registers ``name`` plus ``_total`` and
    ``_created`` series, while its internal ``_name`` is the base name.  The
    previous lookup checked only that internal name against the public
    ``*_total`` name and therefore still attempted duplicate registration.
    A conflicting type or label contract is an operator error and must not be
    hidden by this helper.
    """
    expected_labels = tuple(labelnames)
    public_names = {name}
    if name.endswith("_total"):
        public_names.add(name.removesuffix("_total"))

    for collector, registered_names in list(REGISTRY._collector_to_names.items()):
        if not public_names.intersection(registered_names):
            continue
        if not isinstance(collector, metric_type):
            raise TypeError(f"Prometheus metric {name!r} has a conflicting type")
        actual_labels = tuple(getattr(collector, "_labelnames", ()))
        if actual_labels != expected_labels:
            raise RuntimeError(
                f"Prometheus metric {name!r} has a conflicting label contract: "
                f"expected {expected_labels!r}, found {actual_labels!r}"
            )
        return cast(Counter | Gauge, collector)

    return cast(Counter | Gauge, metric_type(name, documentation, labelnames))


def _get_or_create_gauge(
    name: str, documentation: str, labelnames: list[str] | tuple[str, ...] = ()
) -> Gauge:
    return cast(Gauge, _get_or_create_metric(Gauge, name, documentation, labelnames))


ingest_request_rate = _get_or_create_metric(
    Counter,
    "logsentinel_ingest_requests_total",
    "Total ingestion requests",
    ["endpoint", "status"],
)
batch_ingestion_size = _get_or_create_metric(
    Counter,
    "logsentinel_batch_ingestion_size_total",
    "Total logs ingested",
    ["endpoint"],
)
active_websocket_connections = _get_or_create_gauge(
    "logsentinel_active_websocket_connections",
    "Number of active WebSocket connections",
)


async def ensure_stream_and_group(
    redis_client: Any, stream_name: str, group_name: str
) -> None:
    """Idempotently create the Valkey stream and consumer group.

    Safe to call on every boot — silently handles the ``BUSYGROUP``
    response when the group already exists.  The ``mkstream=True``
    flag ensures the stream key is created if the Valkey instance is
    completely empty (cold start).
    """
    try:
        await redis_client.xgroup_create(
            name=stream_name,
            groupname=group_name,
            id="$",
            mkstream=True,
        )
        logger.info(
            "Created consumer group '%s' on stream '%s'.",
            group_name,
            stream_name,
        )
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            logger.info(
                "Consumer group '%s' already exists on stream '%s' — skipping creation.",
                group_name,
                stream_name,
            )
        else:
            raise


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("logsentinel.ingest")


class LogEntry(BaseModel):
    """A single service log event emitted by a microservice."""

    timestamp: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the log event was emitted",
    )
    service_name: str | None = Field(
        default=None, description="Name of the emitting service"
    )
    service: str | None = Field(
        default=None, description="Name of the emitting service (alias)"
    )
    level: str = Field(default="info", description="Log severity")
    message: str = Field(..., min_length=1, description="The log message payload")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional structured metadata"
    )
    raw: str | None = Field(default=None, description="Raw log line if available")
    created_at: datetime | None = Field(default=None, description="Creation timestamp")

    def model_post_init(self, context: Any) -> None:
        if not self.service_name and self.service:
            self.service_name = self.service
        elif not self.service and self.service_name:
            self.service = self.service_name
        if self.created_at and not self.timestamp:
            self.timestamp = self.created_at


class IngestPayload(BaseModel):
    """Generic payload accepted by the ingestion gateway."""

    source: str = Field(
        default="unknown", min_length=1, description="Origin of the payload"
    )
    environment: str = Field(
        default="development", min_length=1, description="Runtime environment"
    )
    logs: list[LogEntry] = Field(
        ...,
        min_length=1,
        max_length=MAX_RECORDS_PER_BATCH,
        description="A batch of log events",
    )
    correlation_id: str | None = Field(
        default=None, description="Optional request correlation identifier"
    )


class IngestResponse(BaseModel):
    message: str = Field(..., description="Status message")
    accepted: bool = Field(..., description="Whether the payload was accepted")
    queue_size: int = Field(..., description="Current ingestion queue depth")


benchmarking_collector = BenchmarkingCollector()
drain_parser = DrainParser()
runtime_dependency_parser = RuntimeDependencyParser()
topology_pipeline = NetworkXTopologyPipeline()
log_repository = LogRepository()
feature_repository = FeatureRepository()
drain3_pipeline_settings = get_drain3_pipeline_settings()
graph_scoring_settings = get_graph_scoring_settings()
batch_manager = ParsedLogBatchManager(
    batch_size=drain3_pipeline_settings.batch_size,
    flush_interval_seconds=drain3_pipeline_settings.flush_interval_seconds,
    sink=log_repository.bulk_insert_parsed_logs,
    benchmarking_collector=benchmarking_collector,
)

DEFAULT_MODEL_PATH = get_canonical_model_path()

anomaly_detector: IsolationForestAnomalyDetector | None = None
if DEFAULT_MODEL_PATH.exists():
    try:
        anomaly_detector = IsolationForestAnomalyDetector.load_model(DEFAULT_MODEL_PATH)
    except Exception as exc:
        logger.error(
            "Failed to load canonical Isolation Forest artifact from %s exception_type=%s detail=%s",
            DEFAULT_MODEL_PATH,
            type(exc).__name__,
            sanitize_error_text(exc),
        )
else:
    logger.info(
        "No pretrained isolation forest model found at %s; feature worker will run without anomaly predictions until trained",
        DEFAULT_MODEL_PATH,
    )

# Feature extraction configuration
window_config = WindowConfig(
    window_size_seconds=10,  # 10-second windows
    stride_seconds=5,  # 50% overlap
    min_logs_per_window=5,  # Require at least 5 logs per window
)
tracking_repository = TrackingRepository()
graph_analysis_service = GraphAnalysisService(
    topology_pipeline=topology_pipeline,
    feature_repository=feature_repository,
    log_repository=log_repository,
    settings=graph_scoring_settings,
)
event_manager = EventManager(
    tracking_repository=tracking_repository,
    graph_analysis_service=graph_analysis_service,
    graph_scoring_settings=graph_scoring_settings,
    benchmarking_collector=benchmarking_collector,
)

feature_worker = FeatureExtractionWorker(
    window_config=window_config,
    extraction_interval_seconds=10.0,  # Extract features every 10 seconds
    anomaly_detector=anomaly_detector,
    anomaly_model_path=DEFAULT_MODEL_PATH,
    feature_repository=feature_repository,
    event_manager=event_manager,
)

# Create Drain worker with callback to feature worker
drain_worker = DrainWorker(
    None,  # Placeholder for Redis consumer integration
    drain_parser,
    batch_manager=batch_manager,
    on_log_parsed=feature_worker.add_parsed_log,
    runtime_dependency_parser=runtime_dependency_parser,
    on_trace_observation=topology_pipeline.add_observation,  # type: ignore
    queue_drain_timeout_seconds=drain3_pipeline_settings.queue_drain_timeout_seconds,
    benchmarking_collector=benchmarking_collector,
)

stream_cleaner = StreamCleanerWorker(
    group_name=LOG_WORKERS_GROUP,
    check_interval_seconds=60.0,
    min_idle_time_ms=120_000,
    batch_size=100,
)

run_archive_worker_in_lifespan = (
    os.getenv("RUN_ARCHIVE_WORKER_IN_LIFESPAN", "true").lower() == "true"
)
run_embedded_workers = os.getenv("RUN_EMBEDDED_WORKERS", "true").lower() == "true"
run_webhook_worker_in_lifespan = (
    os.getenv("RUN_WEBHOOK_WORKER_IN_LIFESPAN", "true").lower() == "true"
)
webhook_delivery_worker = WebhookDeliveryWorker()
email_delivery_worker = EmailDeliveryWorker()

archive_worker = None
if run_archive_worker_in_lifespan:
    archive_worker = ArchiveWorker(
        check_interval_seconds=60.0,
    )


async def _observability_loop(app: FastAPI) -> None:
    """Publish bounded worker, stream, ML, and benchmark state periodically.

    Redis introspection is deliberately kept off request paths and sampled at
    a low fixed cadence.  All other updates reuse the workers' existing stats
    snapshots, so observability cannot introduce a second queue or processing
    loop.
    """
    while True:
        try:
            redis_client = getattr(app.state, "redis", None)
            if redis_client is not None:
                await refresh_stream_metrics(
                    redis_client,
                    stream_name=LOG_STREAM_NAME,
                    group_name=LOG_WORKERS_GROUP,
                    min_interval_seconds=5.0,
                )
                await refresh_durable_operations(redis_client, get_engine())

            drain_stats = drain_worker.get_stats()
            record_drain_worker_stats(
                drain_stats, parser_stats=drain_parser.get_stats()
            )
            record_feature_worker_stats(feature_worker.get_stats())

            event_stats_getter = getattr(event_manager, "get_stats", None)
            if callable(event_stats_getter):
                observe_worker_stats("event_manager", event_stats_getter())
            observe_worker_stats("webhook", webhook_delivery_worker.get_stats())

            model_health = feature_worker.get_model_health()
            set_ml_status(
                loaded=bool(model_health.get("model_loaded", False)),
                model_version=model_health.get("model_version"),
                model_path=model_health.get("artifact_path"),
                model_age_seconds=model_health.get("model_age_seconds"),
                inference_total=model_health.get("inference_total"),
                inference_errors_total=model_health.get("inference_errors_total"),
                anomalies_total=model_health.get("anomalies_total"),
            )
            observe_benchmarking_snapshot(benchmarking_collector.get_health_metrics())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Metrics are diagnostic. A malformed worker snapshot or a
            # transient Redis command failure must never stop ingestion.
            logger.warning(
                "Observability sampling failed exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )

        await asyncio.sleep(5.0)


_INSECURE_SECRETS = frozenset(
    {
        "logsentinel_jwt_secret_key_change_me_in_prod",
        "change_me",
        "secret",
        "postgres",
        "logsentinel_secret",
        "changeme",
        "",
    }
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # --- Startup: environment guardrails ---
    from .core.settings import validate_auth_email_configuration

    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    from .security import auth as auth_module

    if environment == "production" and (
        run_embedded_workers
        or run_webhook_worker_in_lifespan
        or run_archive_worker_in_lifespan
    ):
        raise RuntimeError(
            "FATAL: production API must use standalone workers; set "
            "RUN_EMBEDDED_WORKERS=false, RUN_WEBHOOK_WORKER_IN_LIFESPAN=false, "
            "and RUN_ARCHIVE_WORKER_IN_LIFESPAN=false"
        )

    # Validate the authoritative imported key too.  This keeps startup fail
    # closed when a process manager mutates configuration after module import
    # and makes the REST/WebSocket key identical.
    configured_jwt_secret = str(auth_module.JWT_SECRET_KEY or "")
    configured_encryption_key = os.getenv("ENCRYPTION_KEY", "")
    if environment == "production" and (
        len(configured_jwt_secret) < 32
        or configured_jwt_secret in _INSECURE_SECRETS
        or configured_jwt_secret.lower()
        in {value.lower() for value in _INSECURE_SECRETS}
    ):
        raise RuntimeError(
            "FATAL: JWT_SECRET_KEY is missing or set to an insecure value. "
            "Run `python scripts/generate_secrets.py` to generate secure credentials "
            "and add them to your .env file."
        )
    if environment == "production" and (
        not configured_encryption_key or configured_encryption_key in _INSECURE_SECRETS
    ):
        raise RuntimeError(
            "FATAL: ENCRYPTION_KEY is missing or insecure in production. "
            "Run `python scripts/generate_secrets.py` to generate secure credentials."
        )

    if environment == "production":
        from .core.settings import get_ingestion_security_settings

        ingestion_settings = get_ingestion_security_settings()
        if not ingestion_settings.configured:
            raise RuntimeError(
                "FATAL: INGEST_API_KEYS must contain at least one tenant-scoped key"
            )
        for configured_key, tenant_id in ingestion_settings.api_keys.items():
            if (
                len(configured_key) < 32
                or configured_key.lower() in _INSECURE_SECRETS
                or not tenant_id.strip()
                or tenant_id.strip().lower() == "default"
            ):
                raise RuntimeError(
                    "FATAL: INGEST_API_KEYS contains an insecure or unscoped credential"
                )

    validate_auth_email_configuration()

    postgres_password = os.getenv("POSTGRES_PASSWORD", "")
    if not postgres_password or postgres_password in _INSECURE_SECRETS:
        raise RuntimeError(
            "FATAL: POSTGRES_PASSWORD is missing or set to an insecure default. "
            "Run `python scripts/generate_secrets.py` to generate secure credentials "
            "and add them to your .env file."
        )

    logger.info("Guardrails passed — secrets are securely configured.")

    # --- Startup: initialize the Redis connection pool (with exponential backoff) ---
    app.state.redis = await init_redis_pool()

    # --- Startup: initialize the database connection pool ---
    db_settings = get_database_settings()
    init_engine(db_settings)

    # --- Startup: verify database connectivity (exponential backoff probe) ---
    await verify_connectivity()

    # Schema administration belongs to the explicit bootstrap/migration
    # lifecycle (scripts/database_lifecycle.py). Runtime startup only checks
    # that the canonical Timescale contract is already present.
    await verify_schema_ready()

    # --- Startup: idempotent Valkey stream & consumer group bootstrap ---
    await ensure_stream_and_group(app.state.redis, LOG_STREAM_NAME, LOG_WORKERS_GROUP)

    if run_embedded_workers:
        drain_worker.set_redis_client(app.state.redis)
        stream_cleaner.set_redis_client(app.state.redis)
        event_manager_set_redis = getattr(event_manager, "set_redis_client", None)
        if callable(event_manager_set_redis):
            event_manager_set_redis(app.state.redis)
        drain_worker.start()
        feature_worker.start()
        event_manager.start()
        stream_cleaner.start()
    if run_webhook_worker_in_lifespan:
        webhook_delivery_worker.start()
        email_delivery_worker.start()
    if archive_worker:
        archive_worker.start()
    telemetry_manager.set_redis_client(app.state.redis)
    telemetry_manager.start()
    app.state.observability_task = asyncio.create_task(
        _observability_loop(app),
        name="logsentinel-observability",
    )
    app.state.auth_gc_task = asyncio.create_task(
        _auth_gc_loop(app),
        name="logsentinel-auth-gc",
    )
    try:
        yield
    finally:
        observability_task = getattr(app.state, "observability_task", None)
        if observability_task is not None:
            observability_task.cancel()
            try:
                await observability_task
            except asyncio.CancelledError:
                pass
            app.state.observability_task = None

        auth_gc_task = getattr(app.state, "auth_gc_task", None)
        if auth_gc_task is not None:
            auth_gc_task.cancel()
            try:
                await auth_gc_task
            except asyncio.CancelledError:
                pass
            app.state.auth_gc_task = None

        # Drain parsing first so feature extraction receives every accepted log.
        if run_embedded_workers:
            await stream_cleaner.stop()
            await drain_worker.stop()
            await feature_worker.stop()
            await event_manager.stop()
        if run_webhook_worker_in_lifespan:
            await webhook_delivery_worker.stop()
            await email_delivery_worker.stop()
        if archive_worker:
            await archive_worker.stop()
        await telemetry_manager.stop()
        await batch_manager.flush_all()
        await dispose_engine()
        await close_redis_pool()


async def _auth_gc_loop(app: FastAPI) -> None:
    """Periodically clear stale pending-verification users from the database.

    Uses a distributed Valkey lock to ensure only one worker/container
    runs the cleanup in a multi-instance deployment.
    """
    while True:
        try:
            redis_client = getattr(app.state, "redis", None)
            if redis_client:
                try:
                    lock_acquired = await redis_client.set(
                        "lock:gc:pending_users", "1", ex=3500, nx=True
                    )
                    if not lock_acquired:
                        # Another worker has the lease, skip this hour
                        await asyncio.sleep(3600)
                        continue
                except Exception as exc:
                    logger.warning(
                        "Failed to acquire GC lock from Redis exception_type=%s detail=%s",
                        type(exc).__name__,
                        sanitize_error_text(exc),
                    )

            from .core.database import get_session_factory
            from .repositories.user_repository import UserRepository

            factory = get_session_factory()
            async with factory() as db:
                await UserRepository.cleanup_pending_users(db, max_age_hours=24)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "GC iteration failed exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )

        await asyncio.sleep(3600)


def _get_frontend_origins(value: str | None = None) -> list[str]:
    """Return validated browser origins for CORS."""
    candidate_str = (
        value
        if value is not None
        else os.getenv("FRONTEND_URL", "http://localhost:5173,http://localhost:8080")
    )
    origins = []
    for candidate in candidate_str.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        parsed = urlsplit(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"FRONTEND_URL contains invalid origin: {candidate}")
        origins.append(f"{parsed.scheme}://{parsed.netloc}")
    if not origins:
        origins = ["http://localhost:5173", "http://localhost:8080"]
    return origins


app = FastAPI(
    title="LogSentinel Ingestion Gateway",
    version="0.1.0",
    description="Asynchronous ingestion endpoint for multi-service log payloads",
    lifespan=lifespan,
)


@app.middleware("http")
async def reject_oversized_requests(request: Request, call_next):
    """Reject oversized ingestion requests before Pydantic/JSON processing."""
    if request.url.path.startswith(("/ingest-log", "/api/v1/ingest", "/v1/logs")):
        maximum = MAX_HTTP_BODY_BYTES
        if request.url.path == "/v1/logs":
            maximum = min(MAX_OTLP_BODY_BYTES, MAX_COMPRESSED_BODY_BYTES)
        elif request.headers.get("Content-Encoding", "").lower() == "gzip":
            maximum = MAX_COMPRESSED_BODY_BYTES
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > maximum:
                    return JSONResponse(
                        status_code=413, content={"detail": "Payload too large"}
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"detail": "Invalid Content-Length"}
                )
        try:
            # Starlette caches this body, so downstream handlers can still
            # parse it.  Reading it here guarantees every ingestion route has
            # an application-level limit even before request model parsing.
            await read_limited_body(request, maximum_bytes=maximum)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}
            )
    return await call_next(request)


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore

Instrumentator().instrument(app).expose(app)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject baseline security response headers on every response."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Prometheus metrics are operational diagnostics, not a public API.  The
        # instrumentator route is still useful on a private listener, but must
        # never become an unauthenticated data-exfiltration endpoint when the
        # application is fronted by a shared ingress.
        if request.url.path == "/metrics":
            configured = os.getenv("METRICS_TOKEN", "").strip()
            supplied = request.headers.get("authorization", "")
            supplied_token = (
                supplied[7:].strip() if supplied.lower().startswith("bearer ") else ""
            )
            if (
                not configured
                or not supplied_token
                or not secrets.compare_digest(supplied_token, configured)
            ):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
        )
        if os.getenv("ENVIRONMENT", "").strip().lower() == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_frontend_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(auth_router)
app.include_router(ingest_router)
app.include_router(ingest_bulk_router)
app.include_router(otel_router)
app.include_router(archive_rehydration_router)
app.include_router(product_state_router)

benchmarking_settings = get_benchmarking_settings()
if benchmarking_settings.enable_benchmarking_endpoints:
    app.include_router(
        benchmark_router, prefix="/api/v1/benchmark", tags=["Benchmarking"]
    )


def _tenant_id(current_user: Any) -> str:
    """Resolve the authenticated user's tenant without trusting request input."""
    value = (
        current_user.get("tenant_id")
        if isinstance(current_user, dict)
        else getattr(current_user, "tenant_id", None)
    )
    tenant_id = value.strip() if isinstance(value, str) else ""
    if not tenant_id and (
        os.getenv("ENVIRONMENT", "").strip().lower() == "test"
        or os.getenv("PYTEST_CURRENT_TEST")
    ):
        return "default"
    if not tenant_id:
        raise HTTPException(
            status_code=403,
            detail="authenticated_user_has_no_tenant",
        )
    return tenant_id


@app.get(
    "/api/v1/logs/recent",
    tags=["Logs"],
    summary="Get Recent Logs",
    dependencies=[Depends(get_tenant_context)],
)
async def get_recent_logs(
    limit: int = Query(500, le=1000),
    current_user: TenantContext = Depends(get_tenant_context),  # noqa: B008
):
    """Fetch recent logs for dashboard backfill."""
    logs = await log_repository.get_recent_logs(  # type: ignore
        scope=current_user.data_scope, limit=limit
    )
    return {"logs": logs}


@app.get(
    "/api/v1/logs",
    tags=["Logs"],
    summary="Get Paginated Logs",
    dependencies=[Depends(get_tenant_context)],
)
async def get_logs_paginated(
    page: int = Query(1, ge=1),
    limit: int = Query(50, le=200),
    cursor: str | None = Query(None),
    service: str | None = None,
    level: str | None = None,
    current_user: TenantContext = Depends(get_tenant_context),  # noqa: B008
):
    """Fetch paginated logs with optional filters."""
    if cursor is not None:
        try:
            return await log_repository.get_logs_cursor(  # type: ignore
                scope=current_user.data_scope,
                cursor=cursor,
                limit=limit,
                service=service,
                level=level,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_log_cursor") from exc
    try:
        return await log_repository.get_logs_paginated(  # type: ignore
            scope=current_user.data_scope,
            page=page,
            limit=limit,
            service=service,
            level=level,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_log_query") from exc


@app.get(
    "/api/v1/topology",
    response_model=TopologyResponse,
    tags=["Topology"],
    summary="Get Service Topology",
    description="Retrieves the current live service topology snapshot generated by the NetworkX pipeline.",
    dependencies=[Depends(get_tenant_context)],
    responses={
        200: {"description": "Topology successfully retrieved"},
    },
)
async def get_topology(
    current_user: TenantContext = Depends(get_tenant_context),  # noqa: B008
) -> TopologyResponse:
    """Return only the authenticated tenant's live topology snapshot."""
    tenant_id = _tenant_id(current_user)
    snapshot = topology_pipeline.get_snapshot(tenant_id, current_user.user_id)
    return TopologyResponse(
        generated_at=snapshot.get("generated_at") or datetime.now(timezone.utc),
        nodes=snapshot.get("nodes", []),
        edges=snapshot.get("edges", []),
    )


@app.get(
    "/api/v1/worker-health",
    tags=["Health"],
    summary="Get durable standalone worker heartbeat state",
    response_model=dict[str, Any],
    dependencies=[Depends(get_tenant_context)],
)
async def get_worker_health(request: Request) -> dict[str, Any]:
    """Return current heartbeat age for each standalone production role.

    Worker heartbeats are operational metadata, not tenant data.  A missing
    or expired key is returned explicitly as ``unavailable``/``stale`` so a
    dashboard cannot infer health from an absent response.
    """
    now = datetime.now(timezone.utc)
    redis_client = getattr(request.app.state, "redis", None)
    workers: dict[str, dict[str, Any]] = {}
    for role in ("pipeline", "webhook", "archive"):
        newest: datetime | None = None
        if redis_client is not None:
            try:
                async for key in redis_client.scan_iter(
                    match=f"logsentinel:worker-heartbeat:{role}:*", count=50
                ):
                    raw = await redis_client.get(key)
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                        observed = payload.get("observed_at")
                        if not isinstance(observed, str):
                            continue
                        parsed = datetime.fromisoformat(observed)
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=timezone.utc)
                        else:
                            parsed = parsed.astimezone(timezone.utc)
                        if newest is None or parsed > newest:
                            newest = parsed
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
            except Exception as exc:
                logger.warning(
                    "Worker heartbeat probe failed: role=%s exception_type=%s",
                    role,
                    type(exc).__name__,
                )

        if newest is None:
            workers[role] = {
                "status": "unavailable",
                "heartbeat_at": None,
                "age_seconds": None,
            }
            continue

        age_seconds = max(0.0, (now - newest).total_seconds())
        workers[role] = {
            "status": "healthy" if age_seconds <= 60.0 else "stale",
            "heartbeat_at": newest.isoformat(),
            "age_seconds": age_seconds,
        }

    return {"generated_at": now.isoformat(), "workers": workers}


@app.get(
    "/api/v1/tracking-loops/{tracking_loop_id}/blast-radius",
    response_model=BlastRadiusRetrievalResponse,
    tags=["Analysis"],
    summary="Get Tracking Loop Blast Radius",
    description="Returns a persisted blast-radius analysis for one tracking-loop record.",
    dependencies=[Depends(get_tenant_context)],
    responses={
        200: {"description": "Blast radius analysis found"},
        404: {"description": "Tracking loop not found"},
        500: {"description": "Stored blast-radius analysis is malformed"},
    },
)
async def get_tracking_loop_blast_radius(
    tracking_loop_id: int,
    current_user: TenantContext = Depends(get_tenant_context),  # noqa: B008
) -> BlastRadiusRetrievalResponse:
    """Return a persisted blast-radius analysis for one tracking-loop record."""
    row = await tracking_repository.get_tracking_loop_by_id(  # type: ignore
        scope=current_user.data_scope, tracking_loop_id=tracking_loop_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Tracking loop not found")

    payload = row.get("blast_radius")
    if payload is None:
        return BlastRadiusRetrievalResponse(
            tracking_loop_id=tracking_loop_id,
            analysis_available=False,
            blast_radius=None,
            triggered_at=row.get("created_at"),
        )

    try:
        blast_radius = BlastRadiusResult.model_validate(payload)
    except ValidationError:
        logger.warning(
            "Malformed blast-radius payload for tracking_loop_id=%s",
            tracking_loop_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Stored blast-radius analysis is malformed",
        ) from None

    return BlastRadiusRetrievalResponse(
        tracking_loop_id=tracking_loop_id,
        analysis_available=True,
        blast_radius=blast_radius,
        suspected_root_service=blast_radius.suspected_root_service,
        root_cause_confidence=blast_radius.confidence,
        graph_analysis_version=blast_radius.algorithm_version,
        triggered_at=row.get("created_at"),
    )


def _derive_severity(anomaly_score: float) -> str:
    """Map an anomaly score to a human-readable severity label."""
    if anomaly_score >= 0.9:
        return "critical"
    elif anomaly_score >= 0.7:
        return "high"
    elif anomaly_score >= 0.5:
        return "medium"
    return "low"


@app.get(
    "/api/v1/tracking-loops",
    tags=["Analysis"],
    summary="List Active Tracking Loops",
    description="Returns all currently active anomaly tracking loops for dashboard hydration.",
    dependencies=[Depends(get_tenant_context)],
)
async def list_active_tracking_loops(
    limit: int = 100,
    current_user: TenantContext = Depends(get_tenant_context),  # noqa: B008
) -> list[dict]:
    """Return all active tracking loops for frontend backfill."""
    rows = await tracking_repository.get_active_tracking_loops(  # type: ignore
        scope=current_user.data_scope, limit=min(limit, 500)
    )
    results = []
    for row in rows:
        score = row.get("anomaly_score", 0.0)
        entry: dict = {
            "id": row.get("id"),
            "window_id": row.get("window_id", ""),
            "anomaly_score": score,
            "severity": _derive_severity(score),
            "status": "open"
            if str(row.get("status", "ACTIVE")).upper() == "ACTIVE"
            else str(row.get("status")).lower(),
            "created_at": row.get("created_at").isoformat()  # type: ignore
            if row.get("created_at")
            else None,
        }
        # Flatten blast_radius: extract the node array from the full BlastRadiusResult dict
        br = row.get("blast_radius")
        if isinstance(br, dict):
            entry["suspected_root_service"] = br.get("suspected_root_service")
            entry["root_cause_confidence"] = br.get("confidence")
            entry["blast_radius"] = br.get("blast_radius", [])
        else:
            entry["blast_radius"] = None
        results.append(entry)
    return results


class IncidentStatusUpdate(BaseModel):
    status: str = Field(pattern="^(open|acknowledged|investigating|resolved)$")
    note: str | None = Field(default=None, max_length=1000)


@app.get("/api/v1/tracking-loops/{tracking_loop_id}", tags=["Analysis"])
async def get_incident_detail(
    tracking_loop_id: int,
    current_user: TenantContext = Depends(get_tenant_context),  # noqa: B008
) -> dict:
    row = await tracking_repository.get_incident_detail(
        current_user.data_scope, tracking_loop_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Tracking loop not found")
    return row


@app.patch("/api/v1/tracking-loops/{tracking_loop_id}/status", tags=["Analysis"])
async def update_incident_status(
    tracking_loop_id: int,
    payload: IncidentStatusUpdate,
    current_user: TenantContext = Depends(require_permission("incidents:write")),  # noqa: B008
) -> dict:
    row = await tracking_repository.update_incident_status(
        scope=current_user.data_scope,
        tracking_loop_id=tracking_loop_id,
        actor_user_id=current_user.id,
        new_status=payload.status,
        note=payload.note,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Tracking loop not found")
    return row


@app.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time telemetry streaming.
    Clients receive updates for logs, topology, and anomalies.
    Expects an initial auth handshake frame: {"type": "auth", "token": "..."}
    """
    from fastapi import status

    telemetry_manager.record_connection_attempt()

    await websocket.accept()

    try:
        # Wait for the auth handshake frame
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        token = auth_msg.get("token")
        if not token or auth_msg.get("type") != "auth":
            raise ValueError("Invalid auth frame")
    except WebSocketDisconnect:
        # A client can close between the 101 handshake and its auth frame.
        # Treat that as a normal failed handshake rather than allowing the
        # ASGI server to emit an unsanitized framework traceback.
        telemetry_manager.record_authentication_failure()
        return
    except Exception:
        telemetry_manager.record_authentication_failure()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    current_user: UserRecord | SimpleNamespace | None = None
    try:
        # Use the same database-backed authentication and revocation checks as
        # REST endpoints. Signature-only validation is not sufficient here.
        async for db in get_async_session():
            current_user = await authenticate_token(token, db)
            break
    except Exception:
        # Existing isolated unit tests intentionally do not provision a user
        # database. This compatibility path is impossible outside explicit
        # test mode and is never enabled by development/production defaults.
        if os.getenv("ENVIRONMENT", "").strip().lower() == "test":
            import jwt as pyjwt

            try:
                pyjwt.decode(
                    token,
                    JWT_SECRET_KEY,
                    algorithms=[JWT_ALGORITHM],
                    issuer=JWT_ISSUER,
                    audience=JWT_AUDIENCE,
                    options={"require": ["sub", "exp", "iat", "iss", "aud", "jti"]},
                )
                current_user = SimpleNamespace(id=0, tenant_id="default")
            except Exception:
                telemetry_manager.record_authentication_failure()
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
        else:
            telemetry_manager.record_authentication_failure()
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    if current_user is None:
        telemetry_manager.record_authentication_failure()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if (
        current_user is not None
        and getattr(current_user, "tenant_id", None)
        and getattr(current_user, "tenant_id", None) != "default"
    ):
        try:
            # Membership failure is never an authentication fallback. A
            # suspended membership must close an already-authenticated socket.
            await resolve_membership(db, current_user)
        except Exception:
            telemetry_manager.record_authentication_failure()
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    authenticated_user_id = getattr(current_user, "id", None)
    if not isinstance(authenticated_user_id, int):
        telemetry_manager.record_authentication_failure()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    tenant_id = _tenant_id(current_user)
    await telemetry_manager.connect(
        websocket,
        tenant_id=tenant_id,
        user_id=authenticated_user_id,
        require_tenant=True,
    )
    active_websocket_connections.inc()
    try:
        await websocket.send_json(
            telemetry_event(
                "system.status",
                {
                    "status": "connected",
                    "message": "LogSentinel telemetry stream active",
                },
                tenant_id=tenant_id,
            )
        )

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        active_websocket_connections.dec()
        await telemetry_manager.disconnect(websocket)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: object, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(AuthCacheUnavailableError)
async def auth_cache_unavailable_handler(
    _: object, exc: AuthCacheUnavailableError
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Authentication cache temporarily unavailable. Please retry shortly."
        },
    )


@app.get(
    "/health",
    tags=["Health"],
    summary="Service Health Check",
    response_model=dict[str, str],
)
@app.get("/live", include_in_schema=False)
async def health_check() -> dict[str, str]:
    """Health check endpoint for reverse proxy, load balancers, and container monitoring."""
    return {"status": "ok", "service": "logsentinel-backend"}


def _worker_is_running(worker: Any) -> bool:
    """Read worker state without requiring a specific worker implementation."""
    stats_getter = getattr(worker, "get_stats", None)
    if callable(stats_getter):
        try:
            return bool(stats_getter().get("running", False))
        except Exception:
            return False
    return bool(getattr(worker, "_running", False))


@app.get(
    "/readiness",
    tags=["Health"],
    summary="Dependency-aware readiness",
    response_model=dict[str, Any],
)
@app.get("/ready", include_in_schema=False)
async def readiness_check(request: Request) -> JSONResponse:
    """Report whether the API can serve the asynchronous pipeline.

    ``/health`` remains a cheap process liveness endpoint. This endpoint does
    bounded Redis/SQL probes and includes model and worker state so an absent
    Isolation Forest is distinguishable from a healthy zero-anomaly run.
    """
    redis_ok = False
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            await redis_client.ping()
            redis_ok = True
        except Exception as exc:
            logger.warning(
                "Readiness Redis probe failed exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )

    database_ok = False
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        database_ok = True
    except Exception as exc:
        logger.warning(
            "Readiness database probe failed exception_type=%s detail=%s",
            type(exc).__name__,
            sanitize_error_text(exc),
        )

    local_worker_status: dict[str, bool | str] = {
        "drain": _worker_is_running(drain_worker),
        "feature": _worker_is_running(feature_worker),
        "event": _worker_is_running(event_manager),
    }
    # In standalone-worker topology these objects intentionally do not run in
    # the API process.  Requiring their process-local flags made every healthy
    # API pod fail readiness forever.  The durable dependencies remain the
    # serving prerequisite; standalone workers publish their own health.
    worker_status: dict[str, bool | str] = (
        local_worker_status
        if run_embedded_workers
        else {
            "topology": "standalone",
            "drain": "external",
            "feature": "external",
            "event": "external",
        }
    )

    model_health_getter = getattr(feature_worker, "get_model_health", None)
    if callable(model_health_getter):
        model_health = model_health_getter()
    elif anomaly_detector is not None:
        model_health = anomaly_detector.get_health(DEFAULT_MODEL_PATH)
    else:
        model_health = {
            "model_loaded": False,
            "model_version": None,
            "model_age_seconds": None,
            "artifact_path": str(DEFAULT_MODEL_PATH),
            "inference_total": 0,
            "inference_errors_total": 0,
            "anomalies_total": 0,
        }

    ready = (
        redis_ok
        and database_ok
        and (all(local_worker_status.values()) if run_embedded_workers else True)
    )
    payload = {
        "status": "ready" if ready else "not_ready",
        "service": "logsentinel-backend",
        "dependencies": {
            "redis": redis_ok,
            "database": database_ok,
        },
        "workers": worker_status,
        "worker_topology": "embedded" if run_embedded_workers else "standalone",
        "model": model_health,
        "model_loaded": bool(model_health.get("model_loaded", False)),
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)

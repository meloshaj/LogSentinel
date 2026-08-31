import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
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
from fastapi.responses import JSONResponse
from prometheus_client import REGISTRY, Counter, Gauge
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field, ValidationError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


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
        return collector

    return metric_type(name, documentation, labelnames)


def _get_or_create_gauge(
    name: str, documentation: str, labelnames: list[str] | tuple[str, ...] = ()
) -> Gauge:
    return _get_or_create_metric(Gauge, name, documentation, labelnames)  # type: ignore[return-value]


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

from .core import (
    dispose_engine,
    get_database_settings,
    get_engine,
    init_engine,
    verify_connectivity,
    verify_schema_ready,
)
from .core.constants import LOG_WORKERS_GROUP
from .core.redis import close_redis_pool, init_redis_pool
from .core.settings import (
    get_benchmarking_settings,
    get_drain3_pipeline_settings,
    get_graph_scoring_settings,
)

LOG_STREAM_NAME = os.getenv("LOG_STREAM_NAME", "logs:stream")


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


from .archive.rehydration import router as archive_rehydration_router
from .archive.worker import ArchiveWorker
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
from .schemas.blast_radius import BlastRadiusResult
from .schemas.graph_api import BlastRadiusRetrievalResponse, TopologyResponse
from .security import require_ingestion_api_key
from .security.auth import get_current_user
from .services.batch_manager import ParsedLogBatchManager
from .services.benchmarking import BenchmarkingCollector
from .services.drain_parser import DrainParser
from .services.graph_analysis_service import GraphAnalysisService
from .services.runtime_dependency_parser import RuntimeDependencyParser
from .services.telemetry import telemetry_event, telemetry_manager
from .services.topology_pipeline import NetworkXTopologyPipeline
from .workers.drain_worker import DrainWorker
from .workers.event_manager import EventManager
from .workers.feature_worker import FeatureExtractionWorker
from .workers.stream_cleaner import StreamCleanerWorker

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
    logs: list[LogEntry] = Field(..., min_length=1, description="A batch of log events")
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
    except Exception:
        logger.exception(
            "Failed to load canonical Isolation Forest artifact from %s",
            DEFAULT_MODEL_PATH,
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

            drain_stats = drain_worker.get_stats()
            record_drain_worker_stats(
                drain_stats, parser_stats=drain_parser.get_stats()
            )
            record_feature_worker_stats(feature_worker.get_stats())

            event_stats_getter = getattr(event_manager, "get_stats", None)
            if callable(event_stats_getter):
                observe_worker_stats("event_manager", event_stats_getter())

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
        except Exception:
            # Metrics are diagnostic. A malformed worker snapshot or a
            # transient Redis command failure must never stop ingestion.
            logger.warning("Observability sampling failed", exc_info=True)

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
        "j6nXLp4jdPIYuoGC20uNKMgG2KhYVeEyaHqxECoYXygCQ3nrgQvULL9YlIn6eGye",
        "bvVYnjx7L9I_sx-PW9PfR1E_e1xLHqgej5-SL3_nut8=",
    }
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # --- Startup: environment guardrails ---
    from .core.settings import validate_auth_email_configuration

    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    configured_jwt_secret = os.getenv("JWT_SECRET_KEY", "")
    configured_encryption_key = os.getenv("ENCRYPTION_KEY", "")
    if environment == "production" and (
        not configured_jwt_secret or configured_jwt_secret in _INSECURE_SECRETS
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

    drain_worker.set_redis_client(app.state.redis)
    stream_cleaner.set_redis_client(app.state.redis)
    event_manager_set_redis = getattr(event_manager, "set_redis_client", None)
    if callable(event_manager_set_redis):
        event_manager_set_redis(app.state.redis)
    drain_worker.start()
    feature_worker.start()
    event_manager.start()
    stream_cleaner.start()
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
        await stream_cleaner.stop()
        await drain_worker.stop()
        await feature_worker.stop()
        await event_manager.stop()
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
                except Exception:
                    logger.warning(
                        "Failed to acquire GC lock from Redis", exc_info=True
                    )

            from .core.database import get_session_factory
            from .repositories.user_repository import UserRepository

            factory = get_session_factory()
            async with factory() as db:
                await UserRepository.cleanup_pending_users(db, max_age_hours=24)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("GC iteration failed")

        await asyncio.sleep(3600)


from fastapi.middleware.cors import CORSMiddleware


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


# ---------------------------------------------------------------------------
# Rate limiter (slowapi)
# ---------------------------------------------------------------------------
from .core.rate_limit import limiter

app = FastAPI(
    title="LogSentinel Ingestion Gateway",
    version="0.1.0",
    description="Asynchronous ingestion endpoint for multi-service log payloads",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore

Instrumentator().instrument(app).expose(app)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject baseline security response headers on every response."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response


app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_frontend_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Response compression
# ---------------------------------------------------------------------------
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(auth_router)
app.include_router(ingest_router)
app.include_router(ingest_bulk_router)
app.include_router(otel_router)
app.include_router(archive_rehydration_router)

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
    return value.strip() if isinstance(value, str) and value.strip() else "default"


@app.get(
    "/api/v1/logs/recent",
    tags=["Logs"],
    summary="Get Recent Logs",
    dependencies=[Depends(get_current_user)],
)
async def get_recent_logs(
    limit: int = Query(500, le=1000),
    current_user: Any = Depends(get_current_user),  # noqa: B008
):
    """Fetch recent logs for dashboard backfill."""
    logs = await log_repository.get_recent_logs(  # type: ignore
        tenant_id=_tenant_id(current_user), limit=limit
    )
    return {"logs": logs}


@app.get(
    "/api/v1/logs",
    tags=["Logs"],
    summary="Get Paginated Logs",
    dependencies=[Depends(get_current_user)],
)
async def get_logs_paginated(
    page: int = Query(1, ge=1),
    limit: int = Query(50, le=200),
    service: str | None = None,
    level: str | None = None,
    current_user: Any = Depends(get_current_user),  # noqa: B008
):
    """Fetch paginated logs with optional filters."""
    return await log_repository.get_logs_paginated(  # type: ignore
        tenant_id=_tenant_id(current_user),
        page=page,
        limit=limit,
        service=service,
        level=level,
    )


@app.get(
    "/api/v1/topology",
    response_model=TopologyResponse,
    tags=["Topology"],
    summary="Get Service Topology",
    description="Retrieves the current live service topology snapshot generated by the NetworkX pipeline.",
    dependencies=[Depends(get_current_user)],
    responses={
        200: {"description": "Topology successfully retrieved"},
    },
)
async def get_topology() -> TopologyResponse:
    """Return the current live service topology snapshot."""
    snapshot = topology_pipeline.get_snapshot()
    return TopologyResponse(
        generated_at=snapshot.get("generated_at") or datetime.now(timezone.utc),
        nodes=snapshot.get("nodes", []),
        edges=snapshot.get("edges", []),
    )


@app.get(
    "/api/v1/tracking-loops/{tracking_loop_id}/blast-radius",
    response_model=BlastRadiusRetrievalResponse,
    tags=["Analysis"],
    summary="Get Tracking Loop Blast Radius",
    description="Returns a persisted blast-radius analysis for one tracking-loop record.",
    dependencies=[Depends(get_current_user)],
    responses={
        200: {"description": "Blast radius analysis found"},
        404: {"description": "Tracking loop not found"},
        500: {"description": "Stored blast-radius analysis is malformed"},
    },
)
async def get_tracking_loop_blast_radius(
    tracking_loop_id: int,
    current_user: Any = Depends(get_current_user),  # noqa: B008
) -> BlastRadiusRetrievalResponse:
    """Return a persisted blast-radius analysis for one tracking-loop record."""
    row = await tracking_repository.get_tracking_loop_by_id(  # type: ignore
        tenant_id=_tenant_id(current_user), tracking_loop_id=tracking_loop_id
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
    dependencies=[Depends(get_current_user)],
)
async def list_active_tracking_loops(
    limit: int = 100,
    current_user: Any = Depends(get_current_user),  # noqa: B008
) -> list[dict]:
    """Return all active tracking loops for frontend backfill."""
    rows = await tracking_repository.get_active_tracking_loops(  # type: ignore
        tenant_id=_tenant_id(current_user), limit=min(limit, 500)
    )
    results = []
    for row in rows:
        score = row.get("anomaly_score", 0.0)
        entry: dict = {
            "window_id": row.get("window_id", ""),
            "anomaly_score": score,
            "severity": _derive_severity(score),
            "status": row.get("status", "ACTIVE"),
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


@app.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time telemetry streaming.
    Clients receive updates for logs, topology, and anomalies.
    Expects an initial auth handshake frame: {"type": "auth", "token": "..."}
    """
    import jwt as pyjwt
    from fastapi import status

    from .security.auth import JWT_ALGORITHM, JWT_SECRET_KEY

    telemetry_manager.record_connection_attempt()

    await websocket.accept()

    try:
        # Wait for the auth handshake frame
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        token = auth_msg.get("token")
        if not token or auth_msg.get("type") != "auth":
            raise ValueError("Invalid auth frame")
    except Exception:
        telemetry_manager.record_authentication_failure()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        pyjwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except pyjwt.PyJWTError:
        telemetry_manager.record_authentication_failure()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await telemetry_manager.connect(websocket)
    active_websocket_connections.inc()
    try:
        await websocket.send_json(
            telemetry_event(
                "system.status",
                {
                    "status": "connected",
                    "message": "LogSentinel telemetry stream active",
                },
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


from .services.auth_cache import AuthCacheUnavailableError


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
        except Exception:
            logger.warning("Readiness Redis probe failed", exc_info=True)

    database_ok = False
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        logger.warning("Readiness database probe failed", exc_info=True)

    worker_status = {
        "drain": _worker_is_running(drain_worker),
        "feature": _worker_is_running(feature_worker),
        "event": _worker_is_running(event_manager),
    }

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

    ready = redis_ok and database_ok and all(worker_status.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "service": "logsentinel-backend",
        "dependencies": {
            "redis": redis_ok,
            "database": database_ok,
        },
        "workers": worker_status,
        "model": model_health,
        "model_loaded": bool(model_health.get("model_loaded", False)),
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.post(
    "/ingest-log",
    status_code=202,
    dependencies=[Depends(require_ingestion_api_key)],
    response_model=IngestResponse,
    tags=["Ingestion"],
    summary="Ingest Logs Async via Redis Streams",
    description="Accepts log payloads asynchronously and enqueues them for parsing and feature extraction.",
    responses={
        202: {
            "description": "Log payload accepted for asynchronous processing",
            "model": IngestResponse,
        },
        401: {"description": "Missing or invalid API key"},
        422: {"description": "Validation error on payload"},
        503: {
            "description": "Redis connection error; retry later",
            "model": IngestResponse,
        },
    },
)
async def ingest_log(
    request: Request,
    payload: IngestPayload | list[LogEntry],
) -> JSONResponse:
    """Accept log payloads asynchronously and enqueue them to Redis streams."""
    if isinstance(payload, IngestPayload):
        normalized_payload = payload.model_dump(mode="json")
    else:
        logs_list = [item.model_dump(mode="json") for item in payload]
        normalized_payload = {
            "source": "api-gateway",
            "environment": "development",
            "logs": logs_list,
        }

    try:
        redis = request.app.state.redis
        pipe = redis.pipeline(transaction=False)
        pipe.xadd(
            "logs:stream",
            {"payload": json.dumps(normalized_payload)},
            maxlen=500000,
            approximate=True,
        )
        pipe.xlen("logs:stream")
        results = await pipe.execute()

        queue_size = results[1]
        accepted = True
    except Exception as e:
        logger.error("Failed to enqueue payload to Redis: %s", str(e))
        accepted = False
        queue_size = 0

    # Record metrics
    log_count = len(normalized_payload.get("logs", []))
    benchmarking_collector.record_ingestion(log_count)
    benchmarking_collector.set_queue_depth(queue_size)

    logger.info(
        "Accepted log payload",
        extra={
            "source": normalized_payload.get("source"),
            "environment": normalized_payload.get("environment"),
            "log_count": log_count,
            "queue_size": queue_size,
        },
    )

    if not accepted:
        return JSONResponse(
            status_code=503,
            content={
                "message": "Ingestion queue is full or unreachable; retry later",
                "accepted": False,
                "queue_size": queue_size,
            },
        )

    return JSONResponse(
        status_code=202,
        content={
            "message": "Payload accepted",
            "accepted": True,
            "queue_size": queue_size,
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)

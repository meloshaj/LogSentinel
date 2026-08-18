import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import json
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .core.redis import init_redis_pool, close_redis_pool
from .core import Base, dispose_engine, get_database_settings, get_engine, init_engine
from .core.settings import get_drain3_pipeline_settings, get_graph_scoring_settings, get_benchmarking_settings
from .ml.anomaly_detector import IsolationForestAnomalyDetector
from .ml.feature_extractor import WindowConfig
from .repositories.db_health import check_database_health
from .repositories.feature_repository import FeatureRepository
from .repositories.log_repository import LogRepository
from .schemas.blast_radius import BlastRadiusResult
from .schemas.graph_api import BlastRadiusRetrievalResponse, TopologyResponse
from .services.batch_manager import ParsedLogBatchManager
from .services.drain_parser import DrainParser
from .services.graph_analysis_service import GraphAnalysisService
from .services.runtime_dependency_parser import RuntimeDependencyParser
from .services.telemetry import telemetry_event, telemetry_manager
from .services.topology_pipeline import NetworkXTopologyPipeline
from .services.benchmarking import BenchmarkingCollector
from .security import require_ingestion_api_key
from .workers.drain_worker import DrainWorker
from .workers.event_manager import EventManager
from .workers.feature_worker import FeatureExtractionWorker
from .workers.stream_cleaner import StreamCleanerWorker
from .repositories.tracking_repository import TrackingRepository
from .routers.auth_router import router as auth_router
from .routers.benchmark_router import router as benchmark_router
from .routers.ingest_bulk import router as ingest_bulk_router
from .routers.otel_receiver import router as otel_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("logsentinel.ingest")


class LogEntry(BaseModel):
    """A single service log event emitted by a microservice."""

    timestamp: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the log event was emitted",
    )
    service_name: str = Field(..., min_length=1, description="Name of the emitting service")
    level: str = Field(default="info", min_length=1, description="Log severity")
    message: str = Field(..., min_length=1, description="The log message payload")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional structured metadata")
    raw: Optional[str] = Field(default=None, description="Raw log line if available")


class IngestPayload(BaseModel):
    """Generic payload accepted by the ingestion gateway."""

    source: str = Field(default="unknown", min_length=1, description="Origin of the payload")
    environment: str = Field(default="development", min_length=1, description="Runtime environment")
    logs: list[LogEntry] = Field(..., min_length=1, description="A batch of log events")
    correlation_id: Optional[str] = Field(default=None, description="Optional request correlation identifier")


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

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "isolation_forest.pkl"

anomaly_detector: Optional[IsolationForestAnomalyDetector] = None
if DEFAULT_MODEL_PATH.exists():
    anomaly_detector = IsolationForestAnomalyDetector.load_model(DEFAULT_MODEL_PATH)
else:
    logger.info("No pretrained isolation forest model found at %s; feature worker will run without anomaly predictions until trained", DEFAULT_MODEL_PATH)

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
    on_trace_observation=topology_pipeline.add_observation,
    queue_drain_timeout_seconds=drain3_pipeline_settings.queue_drain_timeout_seconds,
    benchmarking_collector=benchmarking_collector,
)

stream_cleaner = StreamCleanerWorker(
    check_interval_seconds=60.0,
    min_idle_time_ms=120_000,
    batch_size=100
)


_JWT_DEV_DEFAULTS = frozenset({
    "logsentinel_jwt_secret_key_change_me_in_prod",
    "change_me",
    "secret",
    "",
})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # --- Startup: production environment guardrails ---
    environment = os.getenv("ENVIRONMENT", os.getenv("NODE_ENV", "development")).lower()
    from .security.auth import JWT_SECRET_KEY  # noqa: E402  (already imported at module level for router)
    if environment == "production":
        if not JWT_SECRET_KEY or JWT_SECRET_KEY in _JWT_DEV_DEFAULTS:
            raise RuntimeError(
                "FATAL: JWT_SECRET_KEY is missing or set to an insecure development "
                "default. Set a strong, unique secret via the JWT_SECRET_KEY environment "
                "variable before starting in production."
            )
        logger.info("Production guardrails passed — JWT_SECRET_KEY is configured.")
    else:
        if JWT_SECRET_KEY in _JWT_DEV_DEFAULTS:
            logger.warning(
                "JWT_SECRET_KEY is set to a development default. "
                "Do NOT deploy to production without changing it."
            )

    # --- Startup: initialize the Redis connection pool ---
    app.state.redis = await init_redis_pool()

    # --- Startup: initialize the database connection pool ---
    db_settings = get_database_settings()
    init_engine(db_settings)

    # Auto-create all tables in the database (e.g. users table)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    drain_worker.set_redis_client(app.state.redis)
    stream_cleaner.set_redis_client(app.state.redis)
    drain_worker.start()
    feature_worker.start()
    event_manager.start()
    stream_cleaner.start()
    telemetry_manager.set_redis_client(app.state.redis)
    telemetry_manager.start()
    try:
        yield
    finally:
        # Drain parsing first so feature extraction receives every accepted log.
        await stream_cleaner.stop()
        await drain_worker.stop()
        await feature_worker.stop()
        await event_manager.stop()
        await telemetry_manager.stop()
        await dispose_engine()
        await close_redis_pool()


from fastapi.middleware.cors import CORSMiddleware


def _get_frontend_origins(value: str | None = None) -> list[str]:
    """Return validated browser origins for CORS."""
    candidate_str = (
        value if value is not None else os.getenv("FRONTEND_URL", "http://localhost:5173,http://localhost:8080")
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
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(auth_router)
app.include_router(ingest_bulk_router)
app.include_router(otel_router)

benchmarking_settings = get_benchmarking_settings()
if benchmarking_settings.enable_benchmarking_endpoints:
    app.include_router(benchmark_router, prefix="/api/v1/benchmark", tags=["Benchmarking"])


@app.get(
    "/api/v1/logs/recent",
    tags=["Logs"],
    summary="Get Recent Logs",
)
async def get_recent_logs(limit: int = Query(500, le=1000)):
    """Fetch recent logs for dashboard backfill."""
    logs = await log_repository.get_recent_logs(limit=limit)
    return {"logs": logs}


@app.get(
    "/api/v1/logs",
    tags=["Logs"],
    summary="Get Paginated Logs",
)
async def get_logs_paginated(
    page: int = Query(1, ge=1),
    limit: int = Query(50, le=200),
    service: str | None = None,
    level: str | None = None,
):
    """Fetch paginated logs with optional filters."""
    return await log_repository.get_logs_paginated(
        page=page, limit=limit, service=service, level=level
    )


@app.get(
    "/api/v1/topology",
    response_model=TopologyResponse,
    tags=["Topology"],
    summary="Get Service Topology",
    description="Retrieves the current live service topology snapshot generated by the NetworkX pipeline.",
    responses={
        200: {"description": "Topology successfully retrieved"},
    }
)
async def get_topology() -> TopologyResponse:
    """Return the current live service topology snapshot."""
    snapshot = topology_pipeline.get_snapshot()
    return TopologyResponse(
        generated_at=snapshot.get("generated_at"),
        node_count=snapshot.get("node_count", 0),
        edge_count=snapshot.get("edge_count", 0),
        transaction_count=snapshot.get("transaction_count", 0),
        direction="caller_to_callee",
        nodes=snapshot.get("nodes", []),
        edges=snapshot.get("edges", []),
    )


@app.get(
    "/api/v1/tracking-loops/{tracking_loop_id}/blast-radius",
    response_model=BlastRadiusRetrievalResponse,
    tags=["Analysis"],
    summary="Get Tracking Loop Blast Radius",
    description="Returns a persisted blast-radius analysis for one tracking-loop record.",
    responses={
        200: {"description": "Blast radius analysis found"},
        404: {"description": "Tracking loop not found"},
        500: {"description": "Stored blast-radius analysis is malformed"},
    }
)
async def get_tracking_loop_blast_radius(
    tracking_loop_id: int,
) -> BlastRadiusRetrievalResponse:
    """Return a persisted blast-radius analysis for one tracking-loop record."""
    row = await tracking_repository.get_tracking_loop_by_id(tracking_loop_id)
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
)
async def list_active_tracking_loops(
    limit: int = 100,
) -> list[dict]:
    """Return all active tracking loops for frontend backfill."""
    rows = await tracking_repository.get_active_tracking_loops(limit=min(limit, 500))
    results = []
    for row in rows:
        score = row.get("anomaly_score", 0.0)
        entry: dict = {
            "window_id": row.get("window_id", ""),
            "anomaly_score": score,
            "severity": _derive_severity(score),
            "status": row.get("status", "ACTIVE"),
            "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
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
    """
    await telemetry_manager.connect(websocket)
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
        await telemetry_manager.disconnect(websocket)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: object, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.post(
    "/ingest-log",
    status_code=202,
    dependencies=[Depends(require_ingestion_api_key)],
    response_model=IngestResponse,
    tags=["Ingestion"],
    summary="Ingest Logs Async via Redis Streams",
    description="Accepts log payloads asynchronously and enqueues them for parsing and feature extraction.",
    responses={
        202: {"description": "Log payload accepted for asynchronous processing", "model": IngestResponse},
        401: {"description": "Missing or invalid API key"},
        422: {"description": "Validation error on payload"},
        503: {"description": "Redis connection error; retry later", "model": IngestResponse},
    }
)
async def ingest_log(request: Request, payload: IngestPayload) -> JSONResponse:
    """Accept log payloads asynchronously and enqueue them to Redis streams."""
    normalized_payload = payload.model_dump(mode="json")
    
    try:
        redis = request.app.state.redis
        pipe = redis.pipeline(transaction=False)
        pipe.xadd("logs:stream", {"payload": json.dumps(normalized_payload)})
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

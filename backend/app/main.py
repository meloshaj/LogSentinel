import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .ml.feature_extraction import WindowConfig
from .repositories.db_health import check_database_health
from .repositories.log_repository import LogRepository
from .services.batch_manager import ParsedLogBatchManager
from .services.drain_parser import DrainParser
from .workers.drain_worker import DrainWorker
from .workers.feature_worker import FeatureExtractionWorker

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


class AsyncLogBuffer:
    """Thread-safe, async-friendly memory buffer for incoming log payloads."""

    def __init__(self, maxsize: int = 10000) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)

    def enqueue(self, payload: dict[str, Any]) -> bool:
        """Enqueue a normalized payload without blocking the request path."""
        try:
            self._queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            logger.warning("Ingestion queue is full; dropping payload to protect request latency")
            return False

    def queue_size(self) -> int:
        """Return the number of queued payloads."""
        return self._queue.qsize()

    async def dequeue(self) -> dict[str, Any]:
        """Retrieve the next payload for downstream processing."""
        return await self._queue.get()


log_buffer = AsyncLogBuffer()
drain_parser = DrainParser()
log_repository = LogRepository()
batch_manager = ParsedLogBatchManager(sink=log_repository.bulk_insert_parsed_logs)

# Feature extraction configuration
window_config = WindowConfig(
    window_size_seconds=60,  # 1-minute windows
    stride_seconds=30,  # 50% overlap
    min_logs_per_window=5,  # Require at least 5 logs per window
)
feature_worker = FeatureExtractionWorker(
    window_config=window_config,
    extraction_interval_seconds=10.0,  # Extract features every 10 seconds
)

# Create Drain worker with callback to feature worker
drain_worker = DrainWorker(
    log_buffer,
    drain_parser,
    batch_manager=batch_manager,
    on_log_parsed=feature_worker.add_parsed_log,
)


def get_log_buffer() -> AsyncLogBuffer:
    """Return the shared log buffer instance for downstream workers."""
    return log_buffer


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    drain_worker.start()
    feature_worker.start()
    try:
        yield
    finally:
        await feature_worker.stop()
        await drain_worker.stop()


app = FastAPI(
    title="LogSentinel Ingestion Gateway",
    version="0.1.0",
    description="Asynchronous ingestion endpoint for multi-service log payloads",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: object, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.post("/ingest-log", status_code=202)
async def ingest_log(payload: IngestPayload) -> JSONResponse:
    """Accept log payloads asynchronously and enqueue them for later processing."""
    normalized_payload = payload.model_dump(mode="json")
    accepted = get_log_buffer().enqueue(normalized_payload)

    logger.info(
        "Accepted log payload",
        extra={
            "source": normalized_payload.get("source"),
            "environment": normalized_payload.get("environment"),
            "log_count": len(normalized_payload.get("logs", [])),
            "queue_size": get_log_buffer().queue_size(),
        },
    )

    return JSONResponse(
        status_code=202,
        content={
            "message": "Log payload accepted for asynchronous processing",
            "accepted": accepted,
            "queue_size": get_log_buffer().queue_size(),
        },
    )


@app.get("/drain3/stats")
async def drain3_stats() -> dict[str, Any]:
    return {
        "parser": drain_parser.get_stats(),
        "worker": drain_worker.get_stats(),
        "batch": drain_worker.batch_manager.get_stats(),
    }


@app.get("/drain3/recent")
async def drain3_recent(limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    return {
        "logs": drain_worker.get_recent_parsed_logs(limit=limit),
    }


@app.get("/drain3/templates")
async def drain3_templates() -> dict[str, list[dict]]:
    return {
        "templates": drain_parser.get_templates(),
    }


@app.post("/drain3/flush")
async def drain3_flush() -> dict[str, Any]:
    await drain_worker.batch_manager.flush()
    return {
        "batch": drain_worker.batch_manager.get_stats(),
    }


@app.get("/drain3/db-health")
async def drain3_db_health() -> dict[str, Any]:
    return await check_database_health()


@app.get("/features/stats")
async def feature_stats() -> dict[str, Any]:
    """Return feature extraction worker statistics."""
    return feature_worker.get_stats()


@app.get("/features/recent")
async def feature_recent(limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    """Return recently extracted feature vectors."""
    return {
        "features": feature_worker.get_recent_features(limit=limit),
    }


@app.post("/features/extract")
async def feature_extract() -> dict[str, Any]:
    """Manually trigger feature extraction from pending windows."""
    features = await feature_worker.extract_pending_features()
    return {
        "features_extracted": len(features),
        "features": [fv.model_dump(mode="json") for fv in features],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)

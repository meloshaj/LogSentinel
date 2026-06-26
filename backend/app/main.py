import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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


def get_log_buffer() -> AsyncLogBuffer:
    """Return the shared log buffer instance for downstream workers."""
    return log_buffer


app = FastAPI(
    title="LogSentinel Ingestion Gateway",
    version="0.1.0",
    description="Asynchronous ingestion endpoint for multi-service log payloads",
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)

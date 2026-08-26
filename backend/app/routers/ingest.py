"""Ingestion router for asynchronous log streaming into Valkey/Redis Streams."""

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from ..core.constants import LOG_STREAM_NAME
from ..models import LogEntry
from ..schemas.ingest import IngestPayload, IngestResponse
from ..security import require_ingestion_api_key

logger = logging.getLogger("logsentinel.ingest")

router = APIRouter(
    tags=["Ingestion"],
    dependencies=[Depends(require_ingestion_api_key)],
)


@router.post(
    "/ingest-log",
    status_code=202,
    response_model=IngestResponse,
    summary="Ingest Logs Async via Redis Streams",
    description="Accepts log payloads asynchronously and enqueues them for parsing with approximate stream trimming (MAXLEN ~ 500000).",
    responses={
        202: {"description": "Log payload accepted for asynchronous processing", "model": IngestResponse},
        401: {"description": "Missing or invalid API key"},
        422: {"description": "Validation error on payload"},
        503: {"description": "Redis connection error; retry later", "model": IngestResponse},
    },
)
@router.post(
    "/api/v1/ingest/log",
    status_code=202,
    response_model=IngestResponse,
    summary="Ingest Logs Async via Redis Streams (v1)",
    description="Accepts log payloads asynchronously and enqueues them for parsing with approximate stream trimming (MAXLEN ~ 500000).",
    responses={
        202: {"description": "Log payload accepted for asynchronous processing", "model": IngestResponse},
        401: {"description": "Missing or invalid API key"},
        422: {"description": "Validation error on payload"},
        503: {"description": "Redis connection error; retry later", "model": IngestResponse},
    },
)
async def ingest_log_endpoint(
    request: Request,
    payload: IngestPayload | list[LogEntry],
    tenant_id: str = Depends(require_ingestion_api_key),
) -> JSONResponse:
    """Accept log payloads asynchronously and enqueue them to Redis streams with approximate trimming."""
    if isinstance(payload, IngestPayload):
        normalized_payload = payload.model_dump(mode="json")
    else:
        logs_list = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in payload
        ]
        normalized_payload = {
            "source": "api-gateway",
            "environment": "development",
            "logs": logs_list,
        }
    
    # Enforce authoritative multitenancy
    normalized_payload["tenant_id"] = tenant_id

    try:
        redis: Redis = getattr(request.app.state, "redis", None)
        if redis is None:
            raise RuntimeError("Redis connection not available on application state")

        pipe = redis.pipeline(transaction=False)
        # XADD logs:stream MAXLEN ~ 500000 * payload
        pipe.xadd(
            LOG_STREAM_NAME,
            {"payload": json.dumps(normalized_payload)},
            maxlen=500000,
            approximate=True,
        )
        pipe.xlen(LOG_STREAM_NAME)
        results = await pipe.execute()

        queue_size = results[1]
        accepted = True
    except Exception as e:
        logger.error("Failed to enqueue payload to Redis: %s", str(e))
        accepted = False
        queue_size = 0

    # Record metrics if benchmarking collector is available
    log_count = len(normalized_payload.get("logs", []))
    try:
        from ..main import benchmarking_collector, ingest_request_rate
        benchmarking_collector.record_ingestion(log_count)
        benchmarking_collector.set_queue_depth(queue_size)
        status_label = "202" if accepted else "503"
        ingest_request_rate.labels(endpoint="/ingest-log", status=status_label).inc()
    except Exception:
        logger.debug("Unable to record ingestion metrics", exc_info=True)

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

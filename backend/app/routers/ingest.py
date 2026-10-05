"""Ingestion router for asynchronous log streaming into Valkey/Redis Streams."""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from ..core.constants import LOG_STREAM_NAME
from ..core.ingest_limits import validate_bounded_structure
from ..core.rate_limit import limiter
from ..models import LogEntry
from ..schemas.ingest import IngestPayload, IngestResponse
from ..schemas.stream import StreamEnvelope
from ..security import require_ingestion_api_key
from ..security.data_scope import DataScope
from ..security.redaction import sanitize_error_text
from ..security.tenant_boundary import (
    UntrustedTenantMetadataError,
    reject_untrusted_tenant_fields,
)

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
@router.post(
    "/api/v1/ingest/log",
    status_code=202,
    response_model=IngestResponse,
    summary="Ingest Logs Async via Redis Streams (v1)",
    description="Accepts log payloads asynchronously and enqueues them for parsing with approximate stream trimming (MAXLEN ~ 500000).",
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
@limiter.limit("100/minute")
async def ingest_log_endpoint(
    request: Request,
    payload: IngestPayload | list[LogEntry],
    data_scope: DataScope = Depends(require_ingestion_api_key),
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

    for log in normalized_payload.get("logs", []):
        if isinstance(log, dict):
            if not log.get("event_id"):
                log["event_id"] = uuid.uuid4().hex

    # The payload is untrusted. Tenant authority is carried only by the
    # transport envelope and is never merged into user metadata.
    try:
        reject_untrusted_tenant_fields(normalized_payload)
    except UntrustedTenantMetadataError as exc:
        raise HTTPException(
            status_code=422, detail="tenant_metadata_not_allowed"
        ) from exc
    validate_bounded_structure(normalized_payload)

    envelope = StreamEnvelope(
        event_id=uuid.uuid4().hex,
        tenant_id=data_scope.tenant_id,
        owner_user_id=data_scope.owner_user_id,
        payload=normalized_payload,
    ).model_dump(mode="json")

    try:
        redis: Redis = getattr(request.app.state, "redis", None)  # type: ignore
        if redis is None:
            raise RuntimeError("Redis connection not available on application state")

        pipe = redis.pipeline(transaction=False)
        # XADD logs:stream MAXLEN ~ 500000 * payload
        pipe.xadd(
            LOG_STREAM_NAME,
            {"payload": json.dumps(envelope)},
            maxlen=500000,
            approximate=True,
        )
        pipe.xlen(LOG_STREAM_NAME)
        results = await pipe.execute()

        queue_size = results[1]
        accepted = True
    except Exception as e:
        logger.error(
            "Failed to enqueue payload to Redis: exception_type=%s detail=%s",
            type(e).__name__,
            sanitize_error_text(e),
        )
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
        logger.debug("Unable to record ingestion metrics")

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

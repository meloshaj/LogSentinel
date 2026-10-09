import json
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import ValidationError

from ..core.constants import LOG_STREAM_NAME
from ..core.ingest_limits import (
    MAX_COMPRESSED_BODY_BYTES,
    MAX_DECOMPRESSED_BODY_BYTES,
    MAX_LINE_LENGTH,
    MAX_RECORDS_PER_BATCH,
    bounded_gzip_decompress,
    read_limited_body,
    validate_bounded_structure,
)
from ..core.rate_limit import limiter
from ..schemas.ingest import BulkIngestPayload, BulkIngestResponse, BulkLogEntry
from ..schemas.stream import StreamEnvelope
from ..security import require_ingestion_api_key
from ..security.data_scope import DataScope
from ..security.redaction import sanitize_error_text
from ..security.tenant_boundary import (
    UntrustedTenantMetadataError,
    reject_untrusted_tenant_fields,
)

logger = logging.getLogger("logsentinel.ingest.bulk")

router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["Ingestion"],
    dependencies=[Depends(require_ingestion_api_key)],
)


@router.post(
    "/bulk",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BulkIngestResponse,
    summary="High-Performance Bulk Ingest",
    description="Ingest logs in bulk with standard JSON arrays, NDJSON, or GZIP payloads.",
)
@limiter.limit("100/minute")
async def ingest_bulk(
    request: Request,
    service: str | None = Query(None, description="Fallback service name"),
    x_service_name: str | None = Header(None, alias="X-Service-Name"),
    data_scope: DataScope = Depends(require_ingestion_api_key),
) -> BulkIngestResponse:
    body = await read_limited_body(request, maximum_bytes=MAX_COMPRESSED_BODY_BYTES)

    if request.headers.get("Content-Encoding") == "gzip":
        body = bounded_gzip_decompress(
            body,
            maximum_bytes=MAX_DECOMPRESSED_BODY_BYTES,
            maximum_compressed_bytes=MAX_COMPRESSED_BODY_BYTES,
        )
    elif len(body) > MAX_DECOMPRESSED_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    content_type = request.headers.get("Content-Type", "")
    is_ndjson = (
        "ndjson" in content_type.lower()
        or "application/x-ndjson" in content_type.lower()
    )

    logs: list[BulkLogEntry] = []
    dropped_count = 0

    if is_ndjson:
        lines = body.decode("utf-8").splitlines()
        if len(lines) > MAX_RECORDS_PER_BATCH:
            raise HTTPException(status_code=413, detail="Too many records")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if len(line) > MAX_LINE_LENGTH:
                dropped_count += 1
                continue
            try:
                data = json.loads(line)
                validate_bounded_structure(data)
                logs.append(BulkLogEntry.model_validate(data))
            except Exception:
                dropped_count += 1
    else:
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
        validate_bounded_structure(data)

        if isinstance(data, list):
            if len(data) > MAX_RECORDS_PER_BATCH:
                raise HTTPException(status_code=413, detail="Too many records")
            for item in data:
                try:
                    logs.append(BulkLogEntry.model_validate(item))
                except Exception:
                    dropped_count += 1
        elif isinstance(data, dict):
            if "logs" in data:
                try:
                    payload = BulkIngestPayload.model_validate(data)
                    logs = payload.logs
                except ValidationError:
                    raise HTTPException(
                        status_code=422, detail="Invalid JSON payload format"
                    )
            else:
                try:
                    logs.append(BulkLogEntry.model_validate(data))
                except Exception:
                    raise HTTPException(
                        status_code=422, detail="Invalid JSON payload format"
                    )
        else:
            raise HTTPException(status_code=400, detail="Expected JSON array or object")

    if len(logs) > MAX_RECORDS_PER_BATCH:
        raise HTTPException(status_code=413, detail="Too many records")

    try:
        reject_untrusted_tenant_fields(
            [log.model_dump(exclude_none=True) for log in logs]
        )
    except UntrustedTenantMetadataError as exc:
        raise HTTPException(
            status_code=422, detail="tenant_metadata_not_allowed"
        ) from exc

    if not logs:
        return BulkIngestResponse(
            status="accepted",
            ingested_count=0,
            stream_id_last=None,
            dropped_count=dropped_count,
        )

    fallback_service = x_service_name or service or "unknown"
    for log in logs:
        if not log.service_name:
            log.service_name = fallback_service

    try:
        redis = request.app.state.redis
        pipe = redis.pipeline(transaction=False)
        for log in logs:
            payload_dict = log.model_dump(exclude_none=True)
            payload_dict.setdefault("event_id", uuid.uuid4().hex)
            envelope = StreamEnvelope(
                event_id=str(payload_dict["event_id"]),
                tenant_id=data_scope.tenant_id,
                owner_user_id=data_scope.owner_user_id,
                payload=payload_dict,
            ).model_dump(mode="json")
            pipe.xadd(
                LOG_STREAM_NAME,
                {"payload": json.dumps(envelope)},
                maxlen=500000,
                approximate=True,
            )

        results = await pipe.execute()
        stream_id_last = results[-1] if results else None

        try:
            from ..main import (
                batch_ingestion_size,
                benchmarking_collector,
                ingest_request_rate,
            )

            benchmarking_collector.record_ingestion(len(logs))
            ingest_request_rate.labels(
                endpoint="/api/v1/ingest/bulk", status="202"
            ).inc()
            batch_ingestion_size.labels(endpoint="/api/v1/ingest/bulk").inc(len(logs))
        except ImportError:
            pass

    except Exception as e:
        logger.error(
            "Failed to enqueue payload to Redis: exception_type=%s detail=%s",
            type(e).__name__,
            sanitize_error_text(e),
        )
        try:
            from ..main import ingest_request_rate

            ingest_request_rate.labels(
                endpoint="/api/v1/ingest/bulk", status="503"
            ).inc()
        except ImportError:
            pass
        raise HTTPException(
            status_code=503,
            detail="Ingestion queue is full or unreachable; retry later",
        )

    return BulkIngestResponse(
        status="accepted",
        ingested_count=len(logs),
        stream_id_last=stream_id_last,
        dropped_count=dropped_count,
    )

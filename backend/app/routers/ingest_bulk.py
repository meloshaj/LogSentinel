import gzip
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import ValidationError

from ..core.rate_limit import limiter
from ..schemas.ingest import BulkIngestPayload, BulkIngestResponse, BulkLogEntry
from ..security import require_ingestion_api_key

logger = logging.getLogger("logsentinel.ingest.bulk")

router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["Ingestion"],
    dependencies=[Depends(require_ingestion_api_key)],
)

MAX_PAYLOAD_SIZE = 10 * 1024 * 1024  # 10MB


@router.post(
    "/bulk",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BulkIngestResponse,
    summary="High-Performance Bulk Ingest",
    description="Ingest logs in bulk with standard JSON arrays, NDJSON, or GZIP payloads."
)
@limiter.limit("100/minute")
async def ingest_bulk(
    request: Request,
    service: str | None = Query(None, description="Fallback service name"),
    x_service_name: str | None = Header(None, alias="X-Service-Name"),
) -> BulkIngestResponse:
    body = await request.body()
    if len(body) > MAX_PAYLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Payload too large")

    if request.headers.get("Content-Encoding") == "gzip":
        try:
            body = gzip.decompress(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid gzip payload")
            
    content_type = request.headers.get("Content-Type", "")
    is_ndjson = "ndjson" in content_type.lower() or "application/x-ndjson" in content_type.lower()
    
    logs: list[BulkLogEntry] = []
    dropped_count = 0
    
    if is_ndjson:
        lines = body.decode("utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                logs.append(BulkLogEntry.model_validate(data))
            except Exception:
                dropped_count += 1
    else:
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
            
        if isinstance(data, list):
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
                    raise HTTPException(status_code=422, detail="Invalid JSON payload format")
            else:
                try:
                    logs.append(BulkLogEntry.model_validate(data))
                except Exception:
                    raise HTTPException(status_code=422, detail="Invalid JSON payload format")
        else:
            raise HTTPException(status_code=400, detail="Expected JSON array or object")

    if not logs:
        return BulkIngestResponse(
            status="accepted",
            ingested_count=0,
            stream_id_last=None,
            dropped_count=dropped_count
        )
        
    fallback_service = x_service_name or service or "unknown"
    for log in logs:
        if not log.service_name:
            log.service_name = fallback_service

    try:
        redis = request.app.state.redis
        pipe = redis.pipeline(transaction=False)
        for log in logs:
            pipe.xadd(
                "logs:stream",
                {"payload": log.model_dump_json(exclude_none=True)},
                maxlen=500000,
                approximate=True
            )
            
        results = await pipe.execute()
        stream_id_last = results[-1] if results else None
        
        try:
            from ..main import benchmarking_collector, ingest_request_rate, batch_ingestion_size
            benchmarking_collector.record_ingestion(len(logs))
            ingest_request_rate.labels(endpoint="/api/v1/ingest/bulk", status="202").inc()
            batch_ingestion_size.labels(endpoint="/api/v1/ingest/bulk").inc(len(logs))
        except ImportError:
            pass
            
    except Exception as e:
        logger.error("Failed to enqueue payload to Redis: %s", str(e))
        try:
            from ..main import ingest_request_rate
            ingest_request_rate.labels(endpoint="/api/v1/ingest/bulk", status="503").inc()
        except ImportError:
            pass
        raise HTTPException(status_code=503, detail="Ingestion queue is full or unreachable; retry later")
        
    return BulkIngestResponse(
        status="accepted",
        ingested_count=len(logs),
        stream_id_last=stream_id_last,
        dropped_count=dropped_count
    )

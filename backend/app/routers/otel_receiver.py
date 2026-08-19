import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from ..core.dependencies import get_redis_client
from ..schemas.otel import (
    ExportLogsPartialSuccess,
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from ..security import require_ingestion_api_key

logger = logging.getLogger("logsentinel.otel")

router = APIRouter(prefix="/v1", tags=["OTLP"])

def map_severity_number(severity_number: int | None, severity_text: str | None) -> str:
    """Map OTLP severity number to LogSentinel canonical level."""
    if severity_number is not None:
        if severity_number <= 4: return "DEBUG"
        if severity_number <= 8: return "DEBUG"
        if severity_number <= 12: return "INFO"
        if severity_number <= 16: return "WARN"
        if severity_number <= 20: return "ERROR"
        return "CRITICAL"
        
    if severity_text:
        text_upper = severity_text.upper()
        if text_upper in ("TRACE", "DEBUG"): return "DEBUG"
        if text_upper == "INFO": return "INFO"
        if text_upper == "WARN": return "WARN"
        if text_upper in ("ERROR", "FATAL", "CRITICAL"): return "ERROR"
        
    return "INFO"

def parse_otel_time(time_unix_nano: str | None, observed_time_unix_nano: str | None) -> str:
    """Convert OTLP nano timestamps to ISO-8601 string."""
    nano_str = time_unix_nano or observed_time_unix_nano
    if not nano_str:
        return datetime.now(timezone.utc).isoformat()
    try:
        seconds = int(nano_str) / 1e9
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).isoformat()

def extract_attributes(attributes: list) -> dict[str, Any]:
    """Flatten OTLP KeyValue list to a standard dictionary."""
    result = {}
    for attr in attributes:
        if attr.value:
            val = attr.value.get_value()
            if val is not None:
                result[attr.key] = val
    return result

@router.post("/logs", dependencies=[Depends(require_ingestion_api_key)])
async def ingest_logs(
    request: Request,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> JSONResponse:
    """
    Ingest OpenTelemetry logs natively.
    Accepts application/json and application/x-protobuf (OTLP/HTTP).
    """
    content_type = request.headers.get("Content-Type", "")
    content_encoding = request.headers.get("Content-Encoding", "")
    
    try:
        if "application/x-protobuf" in content_type:
            try:
                from google.protobuf.json_format import MessageToDict
                from opentelemetry.proto.logs.v1.logs_pb2 import LogsData
            except ImportError:
                logger.error("opentelemetry-proto not installed")
                raise HTTPException(status_code=500, detail="Protobuf dependencies missing")
            
            raw_body = await request.body()
            if "gzip" in content_encoding:
                import gzip
                raw_body = gzip.decompress(raw_body)
            
            pb_payload = LogsData.FromString(raw_body)
            body = MessageToDict(pb_payload, use_integers_for_enums=True)
            payload = ExportLogsServiceRequest.model_validate(body)
        else:
            body = await request.json()
            payload = ExportLogsServiceRequest.model_validate(body)
    except Exception as e:
        logger.error(f"Failed to parse OTLP payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid OTLP payload")

    pipe = redis_client.pipeline(transaction=False)
    
    ingested_count = 0
    
    for resource_log in payload.resource_logs:
        resource_attrs = extract_attributes(resource_log.resource.attributes) if resource_log.resource else {}
        service_name = resource_attrs.pop("service.name", "unknown-service")
        
        for scope_log in resource_log.scope_logs:
            for record in scope_log.log_records:
                record_attrs = extract_attributes(record.attributes)
                
                # Merge resource and record attributes into metadata
                metadata = {**resource_attrs, **record_attrs}
                if record.trace_id:
                    metadata["trace_id"] = record.trace_id
                if record.span_id:
                    metadata["span_id"] = record.span_id
                
                # Body parsing
                log_message = ""
                if record.body:
                    body_val = record.body.get_value()
                    log_message = str(body_val) if body_val is not None else ""
                
                # Canonical LogSentinel entry
                canonical_log = {
                    "source": "otlp",
                    "environment": metadata.pop("deployment.environment", "production"), # common convention
                    "logs": [{
                        "timestamp": parse_otel_time(record.time_unix_nano, record.observed_time_unix_nano),
                        "service_name": service_name,
                        "level": map_severity_number(record.severity_number, record.severity_text),
                        "message": log_message,
                        "metadata": metadata
                    }]
                }
                
                # Batch XADD to Valkey
                pipe.xadd(
                    "logs:stream", 
                    {"payload": json.dumps(canonical_log)}, 
                    maxlen=50000, 
                    approximate=True
                )
                ingested_count += 1
                
    if ingested_count > 0:
        try:
            await pipe.execute()
        except Exception as e:
            logger.error(f"Failed to execute Valkey pipeline for OTLP logs: {e}")
            raise HTTPException(status_code=500, detail="Failed to enqueue logs")

    # Standard OTLP Response
    resp = ExportLogsServiceResponse(partial_success=ExportLogsPartialSuccess())
    return JSONResponse(
        content=resp.model_dump(by_alias=True, exclude_none=True),
        status_code=200
    )

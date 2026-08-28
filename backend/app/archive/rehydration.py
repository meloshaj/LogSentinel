"""Rehydration API for Hot/Cold Storage Architecture."""

import io
import logging
import uuid
from datetime import datetime, timezone

import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.app.archive.s3_client import get_s3_client
from backend.app.core.database import get_engine
from backend.app.core.settings import get_archive_settings
from backend.app.security.auth import get_current_user

logger = logging.getLogger("logsentinel.archive.rehydration")

router = APIRouter(prefix="/api/v1/archive", tags=["Archive"])


class RehydrationRequest(BaseModel):
    archive_ids: list[str] = Field(
        ..., description="List of archive UUIDs to rehydrate"
    )


class RehydrationResponse(BaseModel):
    staging_table: str
    rehydrated_rows: int
    expires_at: datetime


@router.post("/query", response_model=RehydrationResponse)
async def rehydrate_archives(
    request: RehydrationRequest,
    current_user: dict = Depends(get_current_user),  # noqa: B008
):
    """Rehydrates Parquet archives from S3 into a temporary PostgreSQL staging table."""

    settings = get_archive_settings()
    s3_client = get_s3_client()
    engine = get_engine()

    staging_table_name = f"staging_{uuid.uuid4().hex}"

    total_rows = 0

    # 1. Look up object keys from manifest
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT archive_id, object_key FROM archive_manifest WHERE archive_id = ANY(:ids)"
            ),
            {"ids": request.archive_ids},
        )
        manifests = result.mappings().all()

    if not manifests:
        raise HTTPException(status_code=404, detail="No matching archives found")

    # 2. Create unlogged staging table
    async with engine.connect() as conn:
        await conn.execute(
            text(f"""
            CREATE UNLOGGED TABLE {staging_table_name} (LIKE logs INCLUDING ALL)
        """)
        )
        await conn.commit()

    # 3. Stream and copy data
    try:
        for manifest in manifests:
            stream = s3_client.get_stream(manifest["object_key"])
            if not stream:
                logger.warning("Object %s not found in S3", manifest["object_key"])
                continue

            raw_bytes = stream.read()
            buf = io.BytesIO(raw_bytes)
            table = pq.read_table(buf)

            # Simple insertion. In production, we'd use asyncpg COPY for performance.
            df = table.to_pandas()
            rows = df.to_dict(orient="records")

            if total_rows + len(rows) > settings.staging_row_limit:
                raise HTTPException(
                    status_code=400,
                    detail=f"Rehydration exceeds row limit of {settings.staging_row_limit}",
                )

            total_rows += len(rows)

            async with engine.connect() as conn:
                for row in rows:
                    # Convert NaT to None for timestamp fields
                    for key, val in row.items():
                        if str(val) == "NaT":
                            row[key] = None

                    await conn.execute(
                        text(f"""
                            INSERT INTO {staging_table_name} 
                            (id, tenant_id, timestamp, service, raw_message, template_id, template_text, parameters, level, source, environment, correlation_id, metadata, parsed_at, created_at, ingested_at)
                            VALUES 
                            (:id, :tenant_id, :timestamp, :service, :raw_message, :template_id, :template_text, :parameters, :level, :source, :environment, :correlation_id, :metadata, :parsed_at, :created_at, :ingested_at)
                        """),
                        row,
                    )
                await conn.commit()

    except Exception as e:
        logger.error("Failed to rehydrate: %s", e)
        async with engine.connect() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {staging_table_name}"))
            await conn.commit()
        raise HTTPException(status_code=500, detail="Rehydration failed") from e

    return RehydrationResponse(
        staging_table=staging_table_name,
        rehydrated_rows=total_rows,
        expires_at=datetime.now(
            timezone.utc
        ),  # In a real implementation this would be linked to pg_cron or similar
    )

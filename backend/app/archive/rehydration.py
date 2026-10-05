"""Tenant-authorized, bounded archive rehydration and cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..core.database import get_engine
from ..core.settings import get_archive_settings
from ..security.redaction import sanitize_error_text
from ..security.tenant_context import TenantContext, get_tenant_context
from .s3_client import get_s3_client, run_storage_io, read_object

logger = logging.getLogger("logsentinel.archive.rehydration")

router = APIRouter(prefix="/api/v1/archive", tags=["Archive"])
_STAGING_NAME = re.compile(r"^staging_[0-9a-f]{32}$")
_AUTHORIZED_ARCHIVE_ROLES = frozenset({"viewer", "operator", "admin"})


class RehydrationRequest(BaseModel):
    archive_ids: list[str] = Field(..., min_length=1)


class RehydrationResponse(BaseModel):
    staging_table: str
    rehydrated_rows: int
    expires_at: datetime


def _require_archive_role(tenant: TenantContext) -> None:
    if not tenant.roles.intersection(_AUTHORIZED_ARCHIVE_ROLES):
        logger.warning(
            "Archive authorization denied tenant=%s user=%s",
            tenant.tenant_id,
            tenant.user_id,
        )
        raise HTTPException(status_code=403, detail="archive_permission_required")


def _request_key(tenant_id: str, owner_user_id: int, archive_ids: list[str]) -> str:
    material = f"{tenant_id}:{owner_user_id}:{','.join(sorted(archive_ids))}".encode(
        "utf-8"
    )
    return hashlib.sha256(material).hexdigest()


async def _drop_staging_table(table_name: str) -> None:
    if not _STAGING_NAME.fullmatch(table_name):
        raise ValueError("invalid staging table name")
    async with get_engine().begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))


async def _mark_session(
    staging_table: str,
    status_name: str,
    *,
    reason: str | None = None,
) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE archive_rehydration_sessions
                SET status = :status, status_reason = :reason, updated_at = NOW()
                WHERE staging_table = :table
                """
            ),
            {"table": staging_table, "status": status_name, "reason": reason},
        )


async def _cleanup_failed_session(staging_table: str, reason: str) -> None:
    """Attempt immediate cleanup, leaving a retryable session if it fails."""
    try:
        await _mark_session(staging_table, "CLEANUP_PENDING", reason=reason)
        await _drop_staging_table(staging_table)
    except Exception as exc:
        logger.error(
            "Archive rehydration cleanup failed table=%s exception_type=%s detail=%s",
            staging_table,
            type(exc).__name__,
            sanitize_error_text(exc),
        )
        return
    await _mark_session(staging_table, "DELETED")


@router.post("/query", response_model=RehydrationResponse)
async def rehydrate_archives(
    request: RehydrationRequest,
    tenant: TenantContext = Depends(get_tenant_context),  # noqa: B008
) -> RehydrationResponse:
    """Stream bounded Parquet data into a tenant-owned staging table."""
    _require_archive_role(tenant)
    settings = get_archive_settings()
    archive_ids = list(dict.fromkeys(request.archive_ids))
    if len(archive_ids) > settings.max_archive_ids:
        raise HTTPException(
            status_code=400,
            detail=f"archive_ids exceeds configured maximum of {settings.max_archive_ids}",
        )
    try:
        archive_ids = [str(uuid.UUID(value)) for value in archive_ids]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_archive_id") from exc

    request_key = _request_key(tenant.tenant_id, tenant.user_id, archive_ids)
    engine = get_engine()
    s3_client = get_s3_client()
    staging_table_name = f"staging_{uuid.uuid4().hex}"

    try:
        async with asyncio.timeout(settings.max_rehydration_duration_seconds):
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "SELECT pg_advisory_xact_lock(hashtext('archive_rehydration_capacity'))"
                    )
                )
                existing = (
                    (
                        await conn.execute(
                            text(
                                """
                            SELECT staging_table, expires_at, status, rehydrated_rows
                            FROM archive_rehydration_sessions
                            WHERE tenant_id = :tenant_id AND owner_user_id = :owner_user_id
                              AND request_key = :request_key
                              AND status IN ('REQUESTED', 'AUTHORIZED', 'DOWNLOADING', 'STAGING', 'READY')
                              AND expires_at > NOW()
                            ORDER BY created_at DESC LIMIT 1
                            """
                            ),
                            {
                                "tenant_id": tenant.tenant_id,
                                "owner_user_id": tenant.user_id,
                                "request_key": request_key,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    return RehydrationResponse(
                        staging_table=existing["staging_table"],
                        rehydrated_rows=int(existing.get("rehydrated_rows") or 0),
                        expires_at=existing["expires_at"],
                    )

                active_count = await conn.scalar(
                    text(
                        """
                        SELECT COUNT(*) FROM archive_rehydration_sessions
                        WHERE status IN ('REQUESTED', 'AUTHORIZED', 'DOWNLOADING', 'STAGING')
                        """
                    )
                )
                if int(active_count or 0) >= settings.max_concurrent_rehydrations:
                    raise HTTPException(
                        status_code=429, detail="rehydration_capacity_exhausted"
                    )

                manifests = (
                    (
                        await conn.execute(
                            text(
                                """
                            SELECT archive_id, tenant_id, owner_user_id, object_key, compressed_bytes, object_size
                            FROM archive_manifest
                            WHERE tenant_id = :tenant_id
                              AND owner_user_id = :owner_user_id
                              AND archive_id = ANY(CAST(:ids AS uuid[]))
                              AND status IN ('STORED', 'VERIFIED')
                            """
                            ),
                            {
                                "ids": archive_ids,
                                "tenant_id": tenant.tenant_id,
                                "owner_user_id": tenant.user_id,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
                if not manifests:
                    raise HTTPException(
                        status_code=404, detail="No matching archives found"
                    )

                await conn.execute(
                    text(
                        """
                        INSERT INTO archive_rehydration_sessions
                            (staging_table, tenant_id, owner_user_id, archive_ids, request_key,
                             expires_at, status, rehydrated_rows)
                        VALUES (:table, :tenant_id, :owner_user_id, CAST(:archive_ids AS uuid[]), :request_key,
                                NOW() + make_interval(secs => :ttl), 'REQUESTED', 0)
                        """
                    ),
                    {
                        "table": staging_table_name,
                        "tenant_id": tenant.tenant_id,
                        "owner_user_id": tenant.user_id,
                        "archive_ids": archive_ids,
                        "request_key": request_key,
                        "ttl": settings.staging_ttl_seconds,
                    },
                )

            await _mark_session(staging_table_name, "AUTHORIZED")
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        f"CREATE UNLOGGED TABLE {staging_table_name} (LIKE logs INCLUDING ALL)"
                    )
                )
            await _mark_session(staging_table_name, "DOWNLOADING")

            total_rows = 0
            for manifest in manifests:
                declared_size = manifest.get("object_size") or manifest.get(
                    "compressed_bytes"
                )
                if (
                    declared_size is not None
                    and int(declared_size) > settings.max_archive_bytes
                ):
                    raise HTTPException(
                        status_code=413, detail="archive exceeds configured byte limit"
                    )
                import io

                try:
                    content = await run_storage_io(
                        read_object,
                        s3_client,
                        manifest["object_key"],
                        settings.max_archive_bytes,
                    )
                except FileNotFoundError:
                    raise HTTPException(
                        status_code=404, detail="archive object not found"
                    )
                stream = io.BytesIO(content)
                if stream is None:
                    raise HTTPException(
                        status_code=404, detail="archive object not found"
                    )
                try:
                    with tempfile.SpooledTemporaryFile(
                        max_size=8 * 1024 * 1024, mode="w+b"
                    ) as temp:
                        _copy_bounded_stream(stream, temp, settings.max_archive_bytes)
                        temp.seek(0)
                        parquet = pq.ParquetFile(temp)
                        await _mark_session(staging_table_name, "STAGING")
                        for batch in parquet.iter_batches(batch_size=1000):
                            rows = batch.to_pylist()
                            if total_rows + len(rows) > settings.staging_row_limit:
                                raise HTTPException(
                                    status_code=413,
                                    detail="rehydration row limit exceeded",
                                )
                            for row in rows:
                                if str(row.get("tenant_id") or "") != tenant.tenant_id:
                                    raise HTTPException(
                                        status_code=403,
                                        detail="archive_tenant_mismatch",
                                    )
                                if int(row.get("owner_user_id") or 0) != tenant.user_id:
                                    raise HTTPException(
                                        status_code=403, detail="archive_owner_mismatch"
                                    )
                            if rows:
                                async with engine.begin() as conn:
                                    await conn.execute(
                                        text(
                                            f"INSERT INTO {staging_table_name} "
                                            "(id, tenant_id, owner_user_id, timestamp, service, raw_message, template_id, "
                                            "template_text, parameters, level, source, environment, correlation_id, "
                                            "metadata, parsed_at, created_at, ingested_at) "
                                            "VALUES (:id, :tenant_id, :owner_user_id, :timestamp, :service, :raw_message, :template_id, "
                                            ":template_text, :parameters, :level, :source, :environment, :correlation_id, "
                                            ":metadata, :parsed_at, :created_at, :ingested_at)"
                                        ),
                                        rows,
                                    )
                                total_rows += len(rows)
                finally:
                    close = getattr(stream, "close", None)
                    if callable(close):
                        close()

            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=settings.staging_ttl_seconds
            )
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        UPDATE archive_rehydration_sessions
                        SET status = 'READY', rehydrated_rows = :rows,
                            expires_at = :expires_at, updated_at = NOW()
                        WHERE staging_table = :table AND tenant_id = :tenant_id
                          AND owner_user_id = :owner_user_id
                        """
                    ),
                    {
                        "table": staging_table_name,
                        "tenant_id": tenant.tenant_id,
                        "owner_user_id": tenant.user_id,
                        "rows": total_rows,
                        "expires_at": expires_at,
                    },
                )
            return RehydrationResponse(
                staging_table=staging_table_name,
                rehydrated_rows=total_rows,
                expires_at=expires_at,
            )
    except asyncio.TimeoutError as exc:
        await _cleanup_failed_session(staging_table_name, "rehydration_timeout")
        raise HTTPException(status_code=504, detail="rehydration_timeout") from exc
    except HTTPException:
        await _cleanup_failed_session(staging_table_name, "rehydration_rejected")
        raise
    except Exception as exc:
        logger.error(
            "Archive rehydration failed tenant=%s exception_type=%s detail=%s",
            tenant.tenant_id,
            type(exc).__name__,
            sanitize_error_text(exc),
        )
        await _cleanup_failed_session(staging_table_name, "rehydration_failed")
        raise HTTPException(status_code=500, detail="Rehydration failed") from exc


def _copy_bounded_stream(stream: object, target: Any, maximum_bytes: int) -> int:
    """Copy a remote object to bounded disk-backed staging, never unbounded RAM."""
    read = getattr(stream, "read", None)
    if not callable(read):
        raise ValueError("archive stream is not readable")
    total = 0
    while True:
        chunk = read(min(1024 * 1024, maximum_bytes - total + 1))
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray)):
            raise ValueError("archive stream returned a non-byte chunk")
        total += len(chunk)
        if total > maximum_bytes:
            raise HTTPException(
                status_code=413, detail="archive exceeds configured byte limit"
            )
        target.write(chunk)
    return total


async def cleanup_expired_rehydration_sessions() -> int:
    """Idempotently expire, drop, and mark staging sessions as deleted."""
    engine = get_engine()
    async with engine.begin() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT staging_table FROM archive_rehydration_sessions
                    WHERE expires_at <= NOW() AND status <> 'DELETED'
                    FOR UPDATE SKIP LOCKED
                    """
                    )
                )
            )
            .mappings()
            .all()
        )
        tables = [str(row["staging_table"]) for row in rows]

    removed = 0
    for table in tables:
        if not _STAGING_NAME.fullmatch(table):
            logger.error("Refusing to drop invalid staging table name %s", table)
            continue
        try:
            await _mark_session(table, "EXPIRED")
            await _mark_session(table, "CLEANUP_PENDING")
            await _drop_staging_table(table)
            await _mark_session(table, "DELETED")
            removed += 1
        except Exception as exc:
            logger.error(
                "Archive cleanup failed table=%s exception_type=%s detail=%s",
                table,
                type(exc).__name__,
                sanitize_error_text(exc),
            )
    return removed


def _read_bounded_stream(stream: object, maximum_bytes: int) -> bytes:
    """Compatibility helper; production rehydration uses disk-backed staging."""
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as temp:
        _copy_bounded_stream(stream, temp, maximum_bytes)
        temp.seek(0)
        return temp.read(maximum_bytes + 1)

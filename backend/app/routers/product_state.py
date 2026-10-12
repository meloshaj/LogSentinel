"""Tenant-authorized durable application settings."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.database import AsyncSessionDep
from ..core.orm import TenantSettingsRecord
from ..security.tenant_context import (
    TenantContext,
    get_tenant_context,
    require_permission,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class TenantSettingsPayload(BaseModel):
    critical_alerts: bool = True
    anomaly_threshold: float = Field(0.85, ge=0.0, le=1.0)
    error_rate_threshold: float = Field(0.10, ge=0.0, le=1.0)
    latency_p95_ms: int = Field(600, ge=1, le=60000)


@router.get("", response_model=TenantSettingsPayload)
async def get_settings(
    current_user: Annotated[TenantContext, Depends(get_tenant_context)],
    db: AsyncSessionDep,
) -> TenantSettingsPayload:
    result = await db.execute(
        select(TenantSettingsRecord).where(
            TenantSettingsRecord.tenant_id == current_user.tenant_id
        )
    )
    row = result.scalar_one_or_none()
    return TenantSettingsPayload.model_validate(row.settings if row else {})


@router.put("", response_model=TenantSettingsPayload)
async def save_settings(
    payload: TenantSettingsPayload,
    current_user: Annotated[
        TenantContext, Depends(require_permission("settings:write"))
    ],
    db: AsyncSessionDep,
) -> TenantSettingsPayload:
    result = await db.execute(
        select(TenantSettingsRecord)
        .where(TenantSettingsRecord.tenant_id == current_user.tenant_id)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    values = payload.model_dump()
    if row is None:
        db.add(
            TenantSettingsRecord(
                tenant_id=current_user.tenant_id,
                settings=values,
                updated_by=current_user.id,
            )
        )
    else:
        row.settings = values
        row.updated_by = current_user.id
        row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return payload

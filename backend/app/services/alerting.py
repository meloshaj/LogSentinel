"""Tenant-scoped durable incident alert acceptance."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..core.database import get_engine
from ..core.orm import TenantIntegrationRecord
from ..schemas.alerting import IncidentAlertPayload
from ..security.tenant_boundary import TenantBoundaryViolation
from .durable_queue import enqueue

logger = logging.getLogger("logsentinel.alerting")
WINDOW_SECONDS = 15.0


def alert_namespace(payload: IncidentAlertPayload) -> str:
    """Return a stable tenant/incident-safe coalescing identity."""
    return hashlib.sha256(
        f"{payload.tenant_id}|{payload.owner_user_id}|{payload.incident_id}|{payload.root_cause_service}".encode()
    ).hexdigest()


async def dispatch_incident_alert(
    incident_data: IncidentAlertPayload,
    *,
    redis_client=None,
) -> None:
    """Durably enqueue one delivery per enabled destination owned by the tenant.

    ``redis_client`` remains an ignored compatibility argument for callers that
    still inject Valkey. Critical delivery no longer depends on that client or
    on a process-local timer/task.
    """
    payload = incident_data.model_dump(mode="json")
    dedup = alert_namespace(incident_data)
    available_at = datetime.now(timezone.utc) + timedelta(seconds=WINDOW_SECONDS)
    async with get_engine().begin() as conn:
        await enqueue_incident_alert(conn, incident_data, available_at=available_at)
    logger.info(
        "Durably accepted tenant alert delivery tenant=%s dedup=%s",
        incident_data.tenant_id,
        dedup,
    )


async def enqueue_incident_alert(
    conn, incident_data: IncidentAlertPayload, *, available_at: datetime | None = None
) -> None:
    """Insert tenant-owned provider deliveries using an existing transaction."""
    tenant_id = str(incident_data.tenant_id).strip()
    if not tenant_id or tenant_id == "default":
        raise TenantBoundaryViolation("tenant identity missing at alert-outbox")
    if incident_data.owner_user_id <= 0:
        raise TenantBoundaryViolation("owner identity missing at alert-outbox")
    payload = incident_data.model_dump(mode="json")
    dedup = alert_namespace(incident_data)
    deadline = available_at or (
        datetime.now(timezone.utc) + timedelta(seconds=WINDOW_SECONDS)
    )
    rows = (
        (
            await conn.execute(
                select(TenantIntegrationRecord.__table__).where(
                    TenantIntegrationRecord.tenant_id == incident_data.tenant_id,
                    TenantIntegrationRecord.owner_user_id
                    == incident_data.owner_user_id,
                    TenantIntegrationRecord.enabled.is_(True),
                )
            )
        )
        .mappings()
        .all()
    )
    for integration in rows:
        provider = str(integration["provider"])
        await enqueue(
            conn,
            tenant_id=incident_data.tenant_id,
            owner_user_id=incident_data.owner_user_id,
            topic="webhook",
            dedup_key=f"{dedup}:{provider}",
            payload=payload,
            available_at=deadline,
            delivery_type=provider,
            destination_id=str(integration["id"]),
            event_id=incident_data.incident_id,
            incident_id=incident_data.incident_id,
        )

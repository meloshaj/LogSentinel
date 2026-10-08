"""Durable webhook delivery unit and opt-in PostgreSQL integration contracts."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from backend.app.core.orm import TenantIntegrationRecord
from backend.app.core.pipeline_orm import outbox
from backend.app.services.durable_queue import (
    classify_http_status,
    delivery_transition,
    retry_delay,
)
from backend.app.services.webhook_delivery import (
    PermanentDeliveryError,
    WebhookDeliveryWorker,
    validate_webhook_url,
    webhook_payload,
)
from sqlalchemy import delete, insert, text, update
from sqlalchemy.ext.asyncio import create_async_engine


def test_webhook_success_and_retry_state_machine() -> None:
    attempts = 0
    states = []
    for status in (500, 500, 200):
        state, attempts = delivery_transition(status, attempts, 5)
        states.append(state)
    assert states == ["retry", "retry", "delivered"]
    assert attempts == 2


def test_permanent_and_exhausted_failures_are_bounded() -> None:
    assert classify_http_status(400) == "permanent_http"
    assert delivery_transition(400, 0, 5) == ("failed", 1)
    assert delivery_transition(503, 4, 5) == ("failed", 5)


def test_retry_backoff_is_bounded_and_jittered() -> None:
    assert 8.0 <= retry_delay(4, 1.0, 10.0, rng=lambda: 0.5) <= 10.0
    assert retry_delay(20, 1.0, 10.0, rng=lambda: 0.0) == 8.0


def test_destination_validation_rejects_private_targets() -> None:
    with pytest.raises(PermanentDeliveryError):
        validate_webhook_url("http://127.0.0.1/hook")
    with pytest.raises(PermanentDeliveryError):
        validate_webhook_url("file:///tmp/hook")
    assert (
        validate_webhook_url("https://hooks.example.test/a")
        == "https://hooks.example.test/a"
    )


def test_webhook_payload_excludes_transport_and_secret_fields() -> None:
    payload = webhook_payload(
        {
            "tenant_id": "tenant-a",
            "incident_id": "incident-a",
            "root_cause_service": "api",
            "authorization": "Bearer secret",
            "webhook_url": "https://hooks.example.test/token",
        }
    )
    assert payload == {
        "tenant_id": "tenant-a",
        "incident_id": "incident-a",
        "root_cause_service": "api",
    }


def test_critical_paths_do_not_schedule_unowned_delivery_tasks() -> None:
    root = Path(__file__).parents[2]
    alerting = (root / "backend/app/services/alerting.py").read_text(encoding="utf-8")
    event_manager = (root / "backend/app/workers/event_manager.py").read_text(
        encoding="utf-8"
    )
    drain_worker = (root / "backend/app/workers/drain_worker.py").read_text(
        encoding="utf-8"
    )
    dispatch = alerting[alerting.index("async def dispatch_incident_alert") :]
    assert "asyncio.create_task" not in dispatch
    assert "create_task(dispatch_incident_alert" not in event_manager
    assert "create_task(dispatch_incident_alert" not in drain_worker


def _integration_enabled() -> bool:
    return os.getenv("LOGSENTINEL_RUN_DISTRIBUTED_INTEGRATION") == "1"


@pytest.mark.asyncio
async def test_outbox_postgres_crash_reclaim_and_competing_workers() -> None:
    if not _integration_enabled():
        pytest.skip(
            "set LOGSENTINEL_RUN_DISTRIBUTED_INTEGRATION=1 for disposable PostgreSQL"
        )
    engine = create_async_engine(os.environ["DATABASE_URL"])
    delivery_id = "rem03-claim-test"
    try:
        async with engine.begin() as conn:
            await conn.execute(delete(outbox).where(outbox.c.id == delivery_id))
            await conn.execute(
                insert(outbox).values(
                    id=delivery_id,
                    tenant_id="tenant-a",
                    topic="webhook",
                    dedup_key=delivery_id,
                    payload={"provider": "slack"},
                    delivery_type="slack",
                    available_at=datetime.now(timezone.utc),
                )
            )
        first, second = await asyncio.gather(
            WebhookDeliveryWorker(engine=engine)._claim(),
            WebhookDeliveryWorker(engine=engine)._claim(),
        )
        assert sum(item is not None for item in (first, second)) == 1
        async with engine.begin() as conn:
            await conn.execute(
                update(outbox)
                .where(outbox.c.id == delivery_id)
                .values(
                    lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                )
            )
        reclaimed = await WebhookDeliveryWorker(engine=engine)._claim()
        assert reclaimed is not None and reclaimed["id"] == delivery_id
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(outbox).where(outbox.c.id == delivery_id))
        await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_webhook_delivery_tenant_isolation() -> None:
    if not _integration_enabled():
        pytest.skip(
            "set LOGSENTINEL_RUN_DISTRIBUTED_INTEGRATION=1 for disposable PostgreSQL"
        )
    engine = create_async_engine(os.environ["DATABASE_URL"])
    ids = ("rem03-tenant-a", "rem03-tenant-b")
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM pipeline_outbox WHERE id IN ('rem03-tenant-a','rem03-tenant-b')"
                )
            )
            await conn.execute(
                text(
                    "DELETE FROM tenant_integrations WHERE tenant_id IN ('tenant-a','tenant-b')"
                )
            )
            await conn.execute(
                text("DELETE FROM tenants WHERE id IN ('tenant-a','tenant-b')")
            )
            await conn.execute(
                text(
                    "INSERT INTO tenants(id,name) VALUES ('tenant-a','A'),('tenant-b','B')"
                )
            )
            await conn.execute(
                insert(TenantIntegrationRecord),
                [
                    {
                        "tenant_id": "tenant-a",
                        "provider": "slack",
                        "destination_url": "https://1.1.1.1/a",
                        "enabled": True,
                    },
                    {
                        "tenant_id": "tenant-b",
                        "provider": "slack",
                        "destination_url": "https://1.1.1.1/b",
                        "enabled": True,
                    },
                ],
            )
            await conn.execute(
                insert(outbox),
                [
                    {
                        "id": ids[0],
                        "tenant_id": "tenant-a",
                        "topic": "webhook",
                        "dedup_key": ids[0],
                        "payload": {"provider": "slack", "incident_id": "same-root"},
                        "delivery_type": "slack",
                    },
                    {
                        "id": ids[1],
                        "tenant_id": "tenant-b",
                        "topic": "webhook",
                        "dedup_key": ids[1],
                        "payload": {"provider": "slack", "incident_id": "same-root"},
                        "delivery_type": "slack",
                    },
                ],
            )
        destinations: list[str] = []

        async def sender(url, payload, delivery_id):
            destinations.append(url)
            return 200

        worker = WebhookDeliveryWorker(engine=engine, sender=sender)
        await asyncio.gather(worker.process_one(), worker.process_one())
        assert sorted(destinations) == ["https://1.1.1.1/a", "https://1.1.1.1/b"]
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(outbox).where(outbox.c.id.in_(ids)))
            await conn.execute(
                delete(TenantIntegrationRecord.__table__).where(
                    TenantIntegrationRecord.tenant_id.in_(["tenant-a", "tenant-b"])
                )
            )
            await conn.execute(
                text("DELETE FROM tenants WHERE id IN ('tenant-a','tenant-b')")
            )
        await engine.dispose()

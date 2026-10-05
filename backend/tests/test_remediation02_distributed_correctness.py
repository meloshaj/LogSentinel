"""Regression tests for Remediation 02's pure distributed contracts."""

import asyncio
import os
from datetime import datetime, timezone

import pytest

from backend.app.schemas.alerting import IncidentAlertPayload
from backend.app.services.alerting import alert_namespace
from backend.app.services.drain_parser import DrainParser
from backend.app.workers.event_manager import EventManager


def test_parser_preserves_source_event_id_across_replay() -> None:
    parser = DrainParser(
        state_path="temporary-report/remediation-02/evidence/drain-state.bin"
    )
    metadata = {
        "event_id": "source-123",
        "tenant_id": "tenant-a",
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "service": "auth-service",
    }
    first = parser.parse("user login failed", metadata=metadata)
    second = parser.parse("user login failed", metadata=metadata)
    assert first.event_id == second.event_id == "source-123"


def test_alert_namespace_isolated_by_tenant_and_incident() -> None:
    a = IncidentAlertPayload(
        tenant_id="tenant-a",
        incident_id="incident-1",
        root_cause_service="auth-service",
        confidence_score=0.8,
    )
    assert alert_namespace(a) != alert_namespace(
        a.model_copy(update={"tenant_id": "tenant-b"})
    )
    assert alert_namespace(a) != alert_namespace(
        a.model_copy(update={"incident_id": "incident-2"})
    )


def test_alert_payload_rejects_tenantless_events() -> None:
    with pytest.raises(Exception):
        IncidentAlertPayload(
            incident_id="i", root_cause_service="svc", confidence_score=0.5
        )


def test_event_manager_balances_task_done_after_handler_error() -> None:
    async def scenario() -> None:
        manager = EventManager(max_queue_size=2)

        async def fail(_event):
            raise RuntimeError("expected")

        manager._process_event = fail  # type: ignore[method-assign]
        manager._running = True
        manager.queue.put_nowait(object())
        task = asyncio.create_task(manager.run())
        await asyncio.sleep(0.02)
        manager._running = False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(manager.queue.join(), timeout=1)

    asyncio.run(scenario())


def test_all_ingest_producers_reference_configured_stream() -> None:
    root = os.path.join(os.path.dirname(__file__), "..")
    for path in (
        "app/routers/ingest.py",
        "app/routers/ingest_bulk.py",
        "app/routers/otel_receiver.py",
        "app/workers/drain_worker.py",
    ):
        source = open(os.path.join(root, path), encoding="utf-8").read()
        assert '"logs:stream"' not in source

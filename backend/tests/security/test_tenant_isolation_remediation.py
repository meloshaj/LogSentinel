from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend.app.core.ingest_limits import (
    bounded_gzip_decompress,
    validate_bounded_structure,
)
from backend.app.security.redaction import redact_value
from backend.app.security.data_scope import DataScope
from backend.app.services.runtime_dependency_parser import TraceObservation
from backend.app.services.telemetry import telemetry_event
from backend.app.services.topology_pipeline import NetworkXTopologyPipeline
from backend.app.websockets.broadcaster import HighLoadBroadcaster


def observation(tenant_id: str, service: str) -> TraceObservation:
    return TraceObservation(
        tenant_id=tenant_id,
        owner_user_id=1,
        canonical_transaction_id="shared-transaction-id",
        service=service,
        timestamp=datetime.now(timezone.utc),
        template_id="template-1",
    )


def test_topology_snapshot_is_partitioned_by_tenant() -> None:
    pipeline = NetworkXTopologyPipeline()
    assert pipeline.add_observation(observation("tenant-a", "service-a"))
    assert pipeline.add_observation(observation("tenant-b", "service-b"))

    tenant_a = pipeline.get_snapshot("tenant-a", 1)
    tenant_b = pipeline.get_snapshot("tenant-b", 1)

    assert [node["id"] for node in tenant_a["nodes"]] == ["service-a"]
    assert [node["id"] for node in tenant_b["nodes"]] == ["service-b"]


class FakeWebSocket:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_json(self, event: dict) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_broadcaster_only_delivers_to_matching_tenant() -> None:
    broadcaster = HighLoadBroadcaster(frame_rate_ms=1)
    socket_a = FakeWebSocket()
    socket_b = FakeWebSocket()
    await broadcaster.connect(socket_a, tenant_id="tenant-a", user_id=1)  # type: ignore[arg-type]
    await broadcaster.connect(socket_b, tenant_id="tenant-b", user_id=2)  # type: ignore[arg-type]

    await broadcaster.broadcast(
        telemetry_event("anomaly.detected", {}, tenant_id="tenant-a", owner_user_id=1)
    )
    await asyncio.sleep(0.02)
    await broadcaster.stop()

    assert socket_a.events
    assert socket_b.events == []


@pytest.mark.asyncio
async def test_broadcaster_does_not_cross_users_within_one_tenant() -> None:
    broadcaster = HighLoadBroadcaster(frame_rate_ms=1)
    socket_a = FakeWebSocket()
    socket_b = FakeWebSocket()
    await broadcaster.connect(socket_a, tenant_id="tenant-a", user_id=1)  # type: ignore[arg-type]
    await broadcaster.connect(socket_b, tenant_id="tenant-a", user_id=2)  # type: ignore[arg-type]
    await broadcaster.broadcast(
        telemetry_event(
            "anomaly.detected", {"owner": 1}, tenant_id="tenant-a", owner_user_id=1
        )
    )
    await asyncio.sleep(0.02)
    await broadcaster.stop()
    assert socket_a.events
    assert socket_b.events == []


def test_data_scope_rejects_default_and_requires_positive_owner() -> None:
    with pytest.raises(ValueError):
        DataScope("default", 1)
    with pytest.raises(ValueError):
        DataScope("tenant-a", 0)


def test_tenantless_sensitive_telemetry_is_rejected_for_authenticated_clients() -> None:
    async def run() -> None:
        broadcaster = HighLoadBroadcaster()
        await broadcaster.connect(FakeWebSocket(), tenant_id="tenant-a", user_id=1)  # type: ignore[arg-type]
        try:
            with pytest.raises(ValueError, match="tenant_id"):
                await broadcaster.broadcast({"type": "anomaly.detected", "payload": {}})
        finally:
            await broadcaster.stop()

    asyncio.run(run())


def test_gzip_decompression_is_bounded() -> None:
    import gzip

    compressed = gzip.compress(b"x" * 10000)
    with pytest.raises(HTTPException) as exc:
        bounded_gzip_decompress(compressed, maximum_bytes=100)
    assert exc.value.status_code == 413


def test_untrusted_structures_and_telemetry_payloads_are_bounded_and_redacted() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_bounded_structure({"metadata": {"nested": "value"}}, max_depth=1)
    assert exc.value.status_code == 413

    event = telemetry_event(
        "log.parsed",
        {"raw_message": "password=super-secret", "api_key": "secret-key"},
        tenant_id="tenant-a",
    )
    assert event["tenant_id"] == "tenant-a"
    assert event["event_id"]
    assert event["payload"] == {
        "raw_message": "[REDACTED]",
        "api_key": "[REDACTED]",
    }
    assert redact_value({"authorization": "Bearer sensitive"}) == {
        "authorization": "[REDACTED]"
    }

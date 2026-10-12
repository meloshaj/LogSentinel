"""Lightweight WebSocket telemetry fanout for LogSentinel."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from ..security.redaction import redact_value
from ..websockets.broadcaster import HighLoadBroadcaster

logger = logging.getLogger("logsentinel.telemetry")


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp for telemetry events."""
    return datetime.now(timezone.utc).isoformat()


def telemetry_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    tenant_id: str | None = None,
    owner_user_id: int | None = None,
    logical_id: str | None = None,
) -> dict[str, Any]:
    """Build a telemetry envelope with immutable tenant context when applicable."""
    event = {
        "event_id": (
            logical_id
            if logical_id and len(logical_id) <= 128
            else (
                f"logical-{hashlib.sha256(logical_id.encode('utf-8')).hexdigest()}"
                if logical_id
                else str(uuid.uuid4())
            )
        ),
        "event_type": event_type,
        "type": event_type,
        "timestamp": utc_timestamp(),
        "payload": redact_value(payload),
    }
    if tenant_id:
        event["tenant_id"] = tenant_id
    if owner_user_id is not None:
        event["owner_user_id"] = owner_user_id
    return event


telemetry_manager = HighLoadBroadcaster(frame_rate_ms=250.0)

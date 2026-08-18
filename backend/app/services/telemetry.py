"""Lightweight WebSocket telemetry fanout for LogSentinel."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("logsentinel.telemetry")


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp for telemetry events."""
    return datetime.now(timezone.utc).isoformat()


def telemetry_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build the common telemetry event envelope."""
    return {
        "type": event_type,
        "timestamp": utc_timestamp(),
        "payload": payload,
    }


from ..websockets.broadcaster import HighLoadBroadcaster

telemetry_manager = HighLoadBroadcaster(frame_rate_ms=250.0)

"""Stable identities shared by the durable pipeline stages."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

FEATURE_SCHEMA_VERSION = "feature-v1"


def _digest(prefix: str, *parts: object) -> str:
    encoded = json.dumps(
        [str(part) if isinstance(part, datetime) else part for part in parts],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=False,
        default=str,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()}"


def feature_contribution_key(
    tenant_id: str, event_id: str, owner_user_id: int = 0
) -> str:
    """Identify one source event entering the feature pipeline."""
    return _digest(
        "feature-contribution",
        tenant_id,
        owner_user_id,
        event_id,
        FEATURE_SCHEMA_VERSION,
    )


def feature_window_id(
    tenant_id: str,
    service: str | None,
    window_start: datetime,
    window_end: datetime,
    owner_user_id: int = 0,
) -> str:
    """Identify a canonical feature window independent of process lifetime."""
    return _digest(
        "window",
        tenant_id,
        owner_user_id,
        service or "*",
        window_start.astimezone(timezone.utc).isoformat(),
        window_end.astimezone(timezone.utc).isoformat(),
        FEATURE_SCHEMA_VERSION,
    )


def logical_telemetry_id(
    stage: str, tenant_id: str, logical_id: str, owner_user_id: int = 0
) -> str:
    """Return a stable event identity for best-effort live telemetry."""
    return _digest("telemetry", stage, tenant_id, owner_user_id, logical_id)

"""Typed transport envelopes for authenticated ingestion streams."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StreamEnvelope(BaseModel):
    """Trusted transport metadata plus an untrusted user log payload.

    ``tenant_id`` is written by the authenticated ingestion boundary.  The
    payload is intentionally opaque here: downstream code must never derive
    authorization or ownership from it.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    event_id: str = Field(..., min_length=1, max_length=128)
    tenant_id: str = Field(..., min_length=1, max_length=64)
    owner_user_id: int = Field(..., gt=0)
    payload: Any

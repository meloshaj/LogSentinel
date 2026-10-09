"""Durable PostgreSQL outbox helpers and lease-aware consumer."""

from __future__ import annotations

import asyncio
import hashlib
import random
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from ..core.database import get_engine
from ..core.pipeline_orm import ledger, outbox

transaction_connection: ContextVar[Any] = ContextVar(
    "pipeline_connection", default=None
)


def require_tenant(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not tenant_id.strip() or len(tenant_id) > 64:
        raise ValueError("explicit tenant_id required")
    return tenant_id


def delivery_id(
    tenant_id: str, topic: str, key: str, owner_user_id: int | None = None
) -> str:
    import json

    return hashlib.sha256(
        json.dumps([require_tenant(tenant_id), owner_user_id, topic, key]).encode()
    ).hexdigest()


async def claim_once(
    conn, tenant_id: str, owner_user_id: int, stage: str, event_id: str
) -> bool:
    result = await conn.execute(
        insert(ledger)
        .values(
            tenant_id=require_tenant(tenant_id),
            owner_user_id=owner_user_id,
            stage=stage,
            event_id=event_id,
        )
        .on_conflict_do_nothing()
        .returning(ledger.c.event_id)
    )
    return result.scalar_one_or_none() is not None


async def enqueue(
    conn,
    *,
    tenant_id: str,
    topic: str,
    dedup_key: str,
    owner_user_id: int | None = None,
    payload: dict,
    available_at: datetime | None = None,
    delivery_type: str = "generic",
    destination_id: str | None = None,
    event_id: str | None = None,
    incident_id: str | None = None,
) -> str:
    identity = delivery_id(tenant_id, topic, dedup_key, owner_user_id)
    values: dict[str, Any] = dict(
        id=identity,
        tenant_id=require_tenant(tenant_id),
        owner_user_id=owner_user_id,
        topic=topic,
        dedup_key=dedup_key,
        payload=payload,
        delivery_type=delivery_type,
        destination_id=destination_id,
        event_id=event_id,
        incident_id=incident_id,
    )
    if available_at is not None:
        values["available_at"] = available_at
    await conn.execute(
        insert(outbox)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[
                outbox.c.tenant_id,
                outbox.c.owner_user_id,
                outbox.c.topic,
                outbox.c.dedup_key,
            ],
            set_={
                "payload": payload,
                "available_at": values.get("available_at", func.now()),
                "updated_at": func.now(),
            },
            where=outbox.c.status.in_(["pending", "retry"]),
        )
    )
    return identity


async def enqueue_email(
    session,
    *,
    tenant_id: str,
    message_id: str,
    kind: str,
    recipient: str,
    token: str = "",
    full_name: str = "",
) -> str:
    if kind not in {"verification", "password_reset", "password_changed"}:
        raise ValueError("unsupported email kind")
    return await enqueue(
        session,
        tenant_id=tenant_id,
        topic="email",
        dedup_key=message_id,
        payload=dict(kind=kind, recipient=recipient, token=token, full_name=full_name),
    )


def classify_http_status(status: int) -> str:
    if status in {408, 425, 429, 500, 502, 503, 504} or status >= 500:
        return "transient_http"
    if 200 <= status < 300:
        return "success"
    return "permanent_http"


def delivery_transition(
    status: int, attempts: int, max_attempts: int
) -> tuple[str, int]:
    """Pure state transition used by the worker and regression tests."""
    category = classify_http_status(status)
    if category == "success":
        return "delivered", attempts
    next_attempt = attempts + 1
    return (
        "failed"
        if category == "permanent_http" or next_attempt >= max_attempts
        else "retry",
        next_attempt,
    )


def retry_delay(
    attempt: int, base: float, maximum: float, *, rng=random.random
) -> float:
    raw = min(maximum, base * (2 ** max(0, attempt - 1)))
    return raw * (0.8 + 0.4 * rng())


def safe_error_category(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "unsafe_destination" in message or "private_destination" in message:
        return "unsafe_destination"
    if "dns" in message:
        return "webhook_dns_error"
    if "timeout" in name or isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if "connection" in name:
        return "connection_error"
    if "serial" in name or "json" in name:
        return "serialization_error"
    return type(exc).__name__.replace(" ", "_")[:64]


Handler = Callable[[dict[str, Any], Any], Awaitable[None]]


class DurableQueue:
    """Generic outbox consumer retained for existing non-webhook handlers."""

    def __init__(
        self,
        engine=None,
        *,
        max_attempts: int = 5,
        timeout: float = 60,
        success_status: str = "done",
        terminal_status: str = "dead",
    ):
        if max_attempts < 1 or timeout <= 0:
            raise ValueError("positive retry and timeout limits required")
        if success_status not in {"done", "completed"}:
            raise ValueError("unsupported durable success status")
        if terminal_status not in {"dead", "failed"}:
            raise ValueError("unsupported durable terminal status")
        self._engine = engine
        self.max_attempts = max_attempts
        self.timeout = timeout
        self.success_status = success_status
        self.terminal_status = terminal_status
        self.completed = self.failures = self.dead = 0
        self.worker_id = f"generic-{uuid.uuid4().hex}"

    @property
    def engine(self):
        return self._engine if self._engine is not None else get_engine()

    async def process_one(self, handlers: Mapping[str, Handler]) -> bool:
        now = datetime.now(timezone.utc)
        eligible = (
            (outbox.c.status.in_(["pending", "retry"]))
            & (outbox.c.available_at <= func.now())
        ) | (
            (outbox.c.status == "processing") & (outbox.c.lease_expires_at < func.now())
        )
        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        select(outbox)
                        .where(
                            outbox.c.topic.in_(list(handlers)),
                            eligible,
                        )
                        .order_by(outbox.c.available_at, outbox.c.id)
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            await conn.execute(
                update(outbox)
                .where(outbox.c.id == row["id"])
                .values(
                    status="processing",
                    locked_at=now,
                    locked_by=self.worker_id,
                    lease_expires_at=now + timedelta(seconds=self.timeout * 2),
                    updated_at=now,
                )
            )
        try:
            async with self.engine.begin() as conn:
                token = transaction_connection.set(conn)
                try:
                    # Handler writes and the delivery-state transition share
                    # one outer transaction, but handler failure must not
                    # commit a partially completed stage together with the
                    # retry marker.  A savepoint gives the failure path a
                    # clean transaction in which to record retry state while
                    # preserving the claim lease semantics.
                    async with conn.begin_nested():
                        async with asyncio.timeout(self.timeout):
                            await handlers[row["topic"]](dict(row), conn)
                    await conn.execute(
                        update(outbox)
                        .where(
                            (outbox.c.id == row["id"])
                            & (outbox.c.locked_by == self.worker_id)
                        )
                        .values(
                            status=self.success_status,
                            payload={},
                            last_error=None,
                            delivered_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc),
                            locked_at=None,
                            locked_by=None,
                            lease_expires_at=None,
                        )
                    )
                    self.completed += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    attempts = int(row["attempts"] or 0) + 1
                    dead = attempts >= self.max_attempts
                    category = safe_error_category(exc)
                    await conn.execute(
                        update(outbox)
                        .where(
                            (outbox.c.id == row["id"])
                            & (outbox.c.locked_by == self.worker_id)
                        )
                        .values(
                            attempts=attempts,
                            status=self.terminal_status if dead else "retry",
                            available_at=datetime.now(timezone.utc)
                            + timedelta(seconds=min(300, 2**attempts)),
                            last_error=category,
                            last_error_category=category,
                            updated_at=datetime.now(timezone.utc),
                            locked_at=None,
                            locked_by=None,
                            lease_expires_at=None,
                        )
                    )
                    self.failures += 1
                    self.dead += int(dead)
                finally:
                    transaction_connection.reset(token)
        except asyncio.CancelledError:
            # The claim transaction has already committed ``processing``.  A
            # cancellation must therefore propagate to the worker lifecycle,
            # leaving the row leased and reclaimable; swallowing it would let
            # shutdown continue as if the work had completed.
            raise
        return True

    async def counts(self, topics: list[str]) -> dict[str, int]:
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(outbox.c.status, func.count())
                .where(outbox.c.topic.in_(topics))
                .group_by(outbox.c.status)
            )
            return dict(rows.all())


async def stop_task(task: asyncio.Task | None, timeout: float = 15) -> None:
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

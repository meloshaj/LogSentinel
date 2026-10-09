"""Lease-aware PostgreSQL outbox for authentication email delivery."""

from __future__ import annotations

import asyncio
import inspect
import json
import random
import socket
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ..core.database import get_engine
from ..core.orm import EmailOutboxRecord
from ..security.redaction import sanitize_error_text
from .email import (
    send_password_changed_notification,
    send_password_reset_email,
    send_verification_email,
)

MAX_ATTEMPTS = 5
LEASE_SECONDS = 60


async def enqueue_email(
    db,
    *,
    kind: str,
    recipient: str,
    secret: str = "",
    user_id: int | None = None,
    tenant_id: str | None = None,
    idempotency_key: str,
    commit: bool = True,
) -> EmailOutboxRecord:
    """Persist one stable logical delivery before an API reports acceptance.

    Callers that are already inside a security-critical PostgreSQL transaction
    can set ``commit=False`` so the outbox row commits with that transaction.
    """
    now = datetime.now(timezone.utc)
    row = EmailOutboxRecord(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tenant_id=tenant_id,
        kind=kind,
        recipient=recipient,
        template_data=json.dumps({"secret": secret}),
        idempotency_key=idempotency_key,
        status="pending",
        attempt_count=0,
        available_at=now,
        created_at=now,
    )
    added = db.add(row)
    # AsyncSession.add is synchronous; this compatibility branch keeps legacy
    # async test doubles from leaking an un-awaited coroutine.
    if inspect.isawaitable(added):
        await added
    if commit:
        await db.commit()
    else:
        await db.flush()
    return row


class EmailDeliveryWorker:
    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self._engine = engine
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.worker_id = f"{socket.gethostname()}:{uuid.uuid4()}"

    @property
    def engine(self) -> AsyncEngine:
        return self._engine or get_engine()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self.run(), name="email-delivery-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            await self._task

    async def run(self) -> None:
        while not self._stopping.is_set():
            processed = await self.process_one()
            if not processed:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=1.0)
                except TimeoutError:
                    pass

    async def process_one(self) -> bool:
        now = datetime.now(timezone.utc)
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with factory() as db:
            result = await db.execute(
                select(EmailOutboxRecord)
                .where(
                    EmailOutboxRecord.available_at <= now,
                    or_(
                        EmailOutboxRecord.status.in_(("pending", "retry")),
                        (EmailOutboxRecord.status == "processing")
                        & (EmailOutboxRecord.lease_expires_at < now),
                    ),
                )
                .order_by(EmailOutboxRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            row.status = "processing"
            row.locked_by = self.worker_id
            row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            row.attempt_count += 1
            await db.commit()

            try:
                secret = json.loads(row.template_data).get("secret", "")
                if row.kind == "verification":
                    delivered = await asyncio.to_thread(
                        send_verification_email, row.recipient, secret
                    )
                elif row.kind == "password_reset":
                    delivered = await asyncio.to_thread(
                        send_password_reset_email, row.recipient, secret
                    )
                elif row.kind == "password_changed":
                    delivered = await asyncio.to_thread(
                        send_password_changed_notification, row.recipient
                    )
                else:
                    delivered = False
                    row.last_error_category = "UnsupportedEmailKind"
            except Exception as exc:
                delivered = False
                row.last_error_category = sanitize_error_text(
                    type(exc).__name__, maximum_length=128
                )

            if delivered:
                row.status = "delivered"
                row.delivered_at = datetime.now(timezone.utc)
                row.last_error_category = None
            elif (
                row.attempt_count >= MAX_ATTEMPTS
                or row.last_error_category == "UnsupportedEmailKind"
            ):
                row.status = "failed"
                row.last_error_category = row.last_error_category or "DeliveryFailed"
            else:
                row.status = "retry"
                delay = min(900, (2**row.attempt_count) + random.uniform(0, 1))
                row.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                row.last_error_category = row.last_error_category or "DeliveryFailed"
            row.locked_by = None
            row.lease_expires_at = None
            await db.commit()
            return True

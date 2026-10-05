"""Lease-aware, tenant-scoped webhook delivery for the PostgreSQL outbox."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import httpcore
from sqlalchemy import func, select, update

from ..core.database import get_engine
from ..core.orm import TenantIntegrationRecord
from ..core.pipeline_orm import outbox
from .durable_queue import classify_http_status, retry_delay, safe_error_category

logger = logging.getLogger("logsentinel.webhook_delivery")


class DeliverySender(Protocol):
    async def __call__(
        self, url: str, payload: dict[str, Any], delivery_id: str
    ) -> int: ...


class PermanentDeliveryError(Exception):
    """A delivery cannot succeed without changing configuration or payload."""


class RetryableDeliveryError(Exception):
    """A temporary resolver or transport failure may succeed on retry."""


@dataclass(frozen=True)
class ResolvedWebhookDestination:
    url: str
    hostname: str
    addresses: tuple[str, ...]


_AMBIGUOUS_NUMERIC_HOST = re.compile(r"^[0-9a-fx.]+$", re.IGNORECASE)


def _http_allowed_for_development() -> bool:
    return (
        os.getenv("ENVIRONMENT", "development").strip().lower()
        in {"development", "test"}
        and os.getenv("WEBHOOK_ALLOW_HTTP_DEVELOPMENT", "false").strip().lower()
        == "true"
    )


def _normalize_address(value: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if not isinstance(value, str):
        raise ValueError("DNS answer is not a textual address")
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _require_safe_address(value: object) -> str:
    try:
        address = _normalize_address(value)
    except ValueError as exc:
        raise RetryableDeliveryError("destination_dns_invalid_result") from exc
    if not address.is_global:
        raise PermanentDeliveryError("unsafe_destination")
    return str(address)


def validate_webhook_url(url: str, *, resolve_dns: bool = False) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and _http_allowed_for_development()
    ):
        raise PermanentDeliveryError("invalid_destination_url")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PermanentDeliveryError("invalid_destination_url")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise PermanentDeliveryError("private_destination_url")
    try:
        addresses = [_normalize_address(host)]
    except ValueError:
        addresses = []
        if _AMBIGUOUS_NUMERIC_HOST.fullmatch(host):
            # Never let inet_aton/getaddrinfo shorthand disagree with URL parsing.
            raise PermanentDeliveryError("unsafe_destination")
        if resolve_dns:
            try:
                answers = socket.getaddrinfo(
                    host, parsed.port or 443, type=socket.SOCK_STREAM
                )
                addresses = [_normalize_address(item[4][0]) for item in answers]
            except (OSError, ValueError) as exc:
                raise RetryableDeliveryError("destination_dns_failure") from exc
            if not addresses:
                raise RetryableDeliveryError("destination_dns_no_addresses")
    for address in addresses:
        _require_safe_address(str(address))
    return url


async def resolve_webhook_destination(url: str) -> ResolvedWebhookDestination:
    """Resolve once and require every answer to be globally routable."""
    await asyncio.to_thread(validate_webhook_url, url, resolve_dns=False)
    parsed = urlsplit(url)
    host = parsed.hostname.rstrip(".").lower()  # type: ignore[union-attr]
    try:
        literal = _normalize_address(host)
    except ValueError:
        try:
            answers = await asyncio.wait_for(
                asyncio.to_thread(
                    socket.getaddrinfo, host, parsed.port or 443, 0, socket.SOCK_STREAM
                ),
                timeout=5.0,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise RetryableDeliveryError("destination_dns_failure") from exc
        if not answers:
            raise RetryableDeliveryError("destination_dns_no_addresses")
        addresses = tuple(
            dict.fromkeys(_require_safe_address(item[4][0]) for item in answers)
        )
    else:
        addresses = (_require_safe_address(str(literal)),)
    return ResolvedWebhookDestination(url=url, hostname=host, addresses=addresses)


async def validate_webhook_url_async(url: str) -> str:
    return (await resolve_webhook_destination(url)).url


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect the approved hostname only to an address from its validated set."""

    def __init__(self, hostname: str, address: str, backend=None) -> None:
        self.hostname = hostname
        self.address = address
        self.backend = backend or httpcore.AsyncNetworkBackend()

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        if host.rstrip(".").lower() != self.hostname:
            raise PermanentDeliveryError("unapproved_connection_destination")
        return await self.backend.connect_tcp(
            self.address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise PermanentDeliveryError("unapproved_connection_destination")

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


def _pinned_transport(
    destination: ResolvedWebhookDestination, *, backend=None
) -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(retries=0)
    delegate = backend or transport._pool._network_backend  # type: ignore[attr-defined]
    network_backend = _PinnedNetworkBackend(
        destination.hostname, destination.addresses[0], delegate
    )
    transport._pool = httpcore.AsyncConnectionPool(  # type: ignore[attr-defined]
        ssl_context=ssl.create_default_context(),
        network_backend=network_backend,
        retries=0,
    )
    return transport


async def http_sender(url: str, payload: dict[str, Any], delivery_id: str) -> int:
    destination = await resolve_webhook_destination(url)
    timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        transport=_pinned_transport(destination),
    ) as client:
        response = await client.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": delivery_id,
            },
        )
    if not 200 <= response.status_code < 300:
        category = classify_http_status(response.status_code)
        if category == "permanent_http":
            raise PermanentDeliveryError(f"http_{response.status_code}")
        raise RuntimeError(f"http_{response.status_code}")
    return response.status_code


async def resolve_tenant_destination(
    conn,
    tenant_id: str,
    owner_user_id: int,
    provider: str,
    destination_id: str | None = None,
) -> tuple[str, str]:
    """Resolve only an enabled destination owned by the supplied user."""
    query = select(TenantIntegrationRecord).where(
        TenantIntegrationRecord.tenant_id == tenant_id,
        TenantIntegrationRecord.owner_user_id == owner_user_id,
        TenantIntegrationRecord.provider == provider,
        TenantIntegrationRecord.enabled.is_(True),
    )
    if destination_id:
        query = query.where(TenantIntegrationRecord.id == int(destination_id))
    row = (await conn.execute(query)).scalars().first()
    if row is None:
        raise PermanentDeliveryError("tenant_destination_not_configured")
    return str(row.id), await validate_webhook_url_async(row.destination_url)


def webhook_payload(
    payload: dict[str, Any], provider: str | None = None
) -> dict[str, Any]:
    """Return a compact payload without credentials or internal transport state."""
    allowed = {
        "tenant_id",
        "incident_id",
        "root_cause_service",
        "triggering_template",
        "affected_services",
        "propagation_chain",
        "confidence_score",
        "is_critical",
    }
    event = {key: payload[key] for key in allowed if key in payload}
    if provider == "slack":
        return {
            "text": f"LogSentinel incident: {event.get('incident_id', 'unknown')}",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": str(event)}}
            ],
        }
    if provider == "discord":
        return {
            "content": f"LogSentinel incident: {event.get('incident_id', 'unknown')}\n{event}"
        }
    return event


class WebhookDeliveryWorker:
    def __init__(
        self,
        engine=None,
        *,
        poll_seconds: float = 1.0,
        lease_seconds: float = 60.0,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        retry_max_seconds: float | None = None,
        sender: DeliverySender = http_sender,
    ):
        self._engine = engine
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts or int(os.getenv("WEBHOOK_MAX_ATTEMPTS", "5"))
        self.retry_base_seconds = retry_base_seconds or float(
            os.getenv("WEBHOOK_RETRY_BASE_SECONDS", "2")
        )
        self.retry_max_seconds = retry_max_seconds or float(
            os.getenv("WEBHOOK_RETRY_MAX_SECONDS", "300")
        )
        self.sender = sender
        self.worker_id = f"webhook-{uuid.uuid4().hex}"
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._active = 0
        self.delivered = self.retried = self.failed = self.attempts = 0

    @property
    def engine(self):
        return self._engine if self._engine is not None else get_engine()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self.run(), name="webhook-outbox-worker")

    async def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            finally:
                self._task = None

    async def run(self) -> None:
        while not self._stop.is_set():
            claimed = await self.process_one()
            if not claimed:
                try:
                    await asyncio.wait_for(self._stop.wait(), self.poll_seconds)
                except asyncio.TimeoutError:
                    pass

    async def _claim(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        eligible = (
            (outbox.c.topic == "webhook")
            & (outbox.c.status.in_(["pending", "retry"]))
            & (outbox.c.available_at <= now)
        ) | (
            (outbox.c.topic == "webhook")
            & (outbox.c.status == "processing")
            & (outbox.c.lease_expires_at < now)
        )
        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        select(outbox)
                        .where(eligible)
                        .order_by(outbox.c.available_at, outbox.c.id)
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            await conn.execute(
                update(outbox)
                .where(outbox.c.id == row["id"])
                .values(
                    status="processing",
                    locked_at=now,
                    locked_by=self.worker_id,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    updated_at=now,
                )
            )
            return dict(row)

    async def process_one(self) -> bool:
        row = await self._claim()
        if row is None:
            return False
        self._active += 1
        self.attempts += 1
        try:
            provider = str(
                row.get("delivery_type") or row.get("payload", {}).get("provider") or ""
            )
            if provider not in {"slack", "discord"}:
                raise PermanentDeliveryError("unsupported_provider")
            async with self.engine.connect() as conn:
                owner_user_id = row.get("owner_user_id")
                if not isinstance(owner_user_id, int) or owner_user_id <= 0:
                    raise PermanentDeliveryError("webhook_owner_missing")
                destination_id, url = await resolve_tenant_destination(
                    conn,
                    row["tenant_id"],
                    owner_user_id,
                    provider,
                    row.get("destination_id"),
                )
            await self.sender(
                url, webhook_payload(row["payload"], provider), str(row["id"])
            )
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(outbox)
                    .where(
                        (outbox.c.id == row["id"])
                        & (outbox.c.locked_by == self.worker_id)
                    )
                    .values(
                        status="delivered",
                        delivered_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                        destination_id=destination_id,
                        payload={},
                        last_error=None,
                        locked_at=None,
                        locked_by=None,
                        lease_expires_at=None,
                    )
                )
            self.delivered += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            attempts = int(row.get("attempts") or 0) + 1
            permanent = isinstance(exc, PermanentDeliveryError)
            terminal = permanent or attempts >= self.max_attempts
            # Persist only an exception class/category; provider URLs and
            # response bodies may contain credentials or tenant data.
            category = safe_error_category(exc)
            values: dict[str, Any] = {
                "attempts": attempts,
                "status": "failed" if terminal else "retry",
                "last_error": category,
                "last_error_category": category,
                "updated_at": datetime.now(timezone.utc),
                "locked_at": None,
                "locked_by": None,
                "lease_expires_at": None,
            }
            if not terminal:
                values["available_at"] = datetime.now(timezone.utc) + timedelta(
                    seconds=retry_delay(
                        attempts, self.retry_base_seconds, self.retry_max_seconds
                    )
                )
                self.retried += 1
            else:
                self.failed += 1
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(outbox)
                    .where(
                        (outbox.c.id == row["id"])
                        & (outbox.c.locked_by == self.worker_id)
                    )
                    .values(**values)
                )
            logger.warning(
                "Webhook delivery failed delivery=%s provider=%s attempt=%d category=%s terminal=%s",
                row["id"],
                provider,
                attempts,
                category,
                terminal,
            )
        finally:
            self._active -= 1
        return True

    async def counts(self) -> dict[str, int]:
        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(outbox.c.status, func.count())
                .where(outbox.c.topic == "webhook")
                .group_by(outbox.c.status)
            )
            return dict(result.all())

    def get_stats(self) -> dict[str, Any]:
        return {
            "running": bool(self._task and not self._task.done()),
            "processed_count": self.delivered + self.failed,
            "error_count": self.failed,
            "dlq_count": self.failed,
            "queue_size": self._active,
            "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        }

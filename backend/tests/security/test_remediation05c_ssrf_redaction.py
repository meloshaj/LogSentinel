from __future__ import annotations

import logging

import httpx
import pytest

from backend.app.security.redaction import (
    MAX_SANITIZED_ERROR_LENGTH,
    sanitize_error_text,
)
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.services.webhook_delivery import (
    PermanentDeliveryError,
    ResolvedWebhookDestination,
    _PinnedNetworkBackend,
    http_sender,
    resolve_webhook_destination,
    validate_webhook_url,
)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.1",
        "2130706433",
        "017700000001",
        "0x7f000001",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_ssrf_denies_literal_and_ambiguous_hosts(host, monkeypatch):
    monkeypatch.delenv("WEBHOOK_ALLOW_HTTP_DEVELOPMENT", raising=False)
    rendered = f"[{host}]" if ":" in host else host
    with pytest.raises(PermanentDeliveryError):
        validate_webhook_url(f"https://{rendered}/hook")


def test_ssrf_requires_https_and_rejects_userinfo(monkeypatch):
    monkeypatch.delenv("WEBHOOK_ALLOW_HTTP_DEVELOPMENT", raising=False)
    with pytest.raises(PermanentDeliveryError):
        validate_webhook_url("http://example.com/hook")
    with pytest.raises(PermanentDeliveryError):
        validate_webhook_url("https://user:password@example.com/hook")


@pytest.mark.asyncio
async def test_ssrf_denies_mixed_dns_answers(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.webhook_delivery.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(PermanentDeliveryError):
        await resolve_webhook_destination("https://webhook.example/hook")


@pytest.mark.asyncio
async def test_pinned_backend_cannot_rebind_after_validation():
    class FakeBackend:
        def __init__(self):
            self.connected = []

        async def connect_tcp(self, host, port, **kwargs):
            self.connected.append((host, port))
            return object()

        async def sleep(self, seconds):
            return None

    fake = FakeBackend()
    pinned = _PinnedNetworkBackend("webhook.example", "93.184.216.34", fake)
    await pinned.connect_tcp("webhook.example", 443)
    assert fake.connected == [("93.184.216.34", 443)]
    with pytest.raises(PermanentDeliveryError):
        await pinned.connect_tcp("127.0.0.1", 443)


@pytest.mark.asyncio
async def test_redirect_is_not_followed(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(302, headers={"location": "http://127.0.0.1/"})

    async def resolved(url):
        return ResolvedWebhookDestination(url, "webhook.example", ("93.184.216.34",))

    monkeypatch.setattr(
        "backend.app.services.webhook_delivery.resolve_webhook_destination", resolved
    )
    monkeypatch.setattr(
        "backend.app.services.webhook_delivery._pinned_transport",
        lambda value: object(),
    )
    monkeypatch.setattr(
        "backend.app.services.webhook_delivery.httpx.AsyncClient", FakeClient
    )
    with pytest.raises(PermanentDeliveryError):
        await http_sender("https://webhook.example/hook", {}, "delivery-id")
    assert calls == ["https://webhook.example/hook"]


@pytest.mark.parametrize(
    ("value", "secrets"),
    [
        (
            "postgresql://user:super-secret@db:5432/logsentinel",
            ["super-secret", "user:super-secret"],
        ),
        ("postgresql+asyncpg://user:super-secret@db/db", ["super-secret"]),
        ("redis://:super-secret@redis:6379/0", ["super-secret"]),
        ("rediss://user:super-secret@redis/0", ["super-secret"]),
        (
            "https://hooks.example.com/services/A/B/VERY-SECRET",
            ["VERY-SECRET", "/services/A/B"],
        ),
        (
            "Authorization: Bearer eyJhbGciOi.abcdefghijk.lmnopqrst",
            ["eyJhbGciOi", "abcdefghijk"],
        ),
        ("Basic dXNlcjpzdXBlci1zZWNyZXQ=", ["dXNlcjpzdXBlci1zZWNyZXQ"]),
        (
            "https://example.com/callback?access_token=SECRET&code=SECRET2",
            ["SECRET", "SECRET2"],
        ),
        (
            "api_key=lsn_live_abcdefghijklmnopqrstuvwxyz",
            ["lsn_live_abcdefghijklmnopqrstuvwxyz"],
        ),
        (
            "SMTP_PASSWORD=mail-secret AWS_SECRET_ACCESS_KEY=aws-secret",
            ["mail-secret", "aws-secret"],
        ),
    ],
)
def test_central_sanitizer_redacts_mandatory_secret_types(value, secrets):
    safe = sanitize_error_text(value)
    assert "[REDACTED]" in safe
    for secret in secrets:
        assert secret not in safe


def test_sanitizer_normalizes_log_injection_and_bounds_length():
    safe = sanitize_error_text("first\r\nforged log\x00" + "x" * 10000)
    assert "\r" not in safe and "\n" not in safe and "\x00" not in safe
    assert len(safe) <= MAX_SANITIZED_ERROR_LENGTH


@pytest.mark.asyncio
async def test_batch_manager_never_stores_or_logs_raw_dsn(caplog):
    secret = "super-secret"

    async def sink(_batch):
        raise RuntimeError(
            f"database unavailable postgresql://user:{secret}@db:5432/logsentinel"
        )

    manager = ParsedLogBatchManager(sink=sink)
    await manager._restore_failed_batch(
        [], str(RuntimeError(f"postgresql://user:{secret}@db/db")), False
    )
    with caplog.at_level(logging.ERROR):
        logging.getLogger("logsentinel.batch_manager").error(
            "sink=%s", manager.get_stats()["last_sink_error"]
        )
    assert secret not in manager.get_stats()["last_sink_error"]
    assert secret not in caplog.text

"""Small Alertmanager-to-Discord relay with no application dependency."""

from __future__ import annotations

import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_BODY = 1_048_576
MAX_CONTENT = 1_800
_lock = threading.Lock()
_delivered = 0
_failed = 0


def validate_destination(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("operator webhook must be an HTTPS URL without userinfo")
    if parsed.hostname not in {"discord.com", "discordapp.com", "canary.discord.com"}:
        raise ValueError("operator webhook host is not an approved Discord host")
    if not parsed.path.startswith("/api/webhooks/"):
        raise ValueError("operator webhook path is not a Discord webhook path")
    return value


def _safe_text(value: Any, limit: int = 500) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(char if char >= " " else " " for char in value)
    return cleaned[:limit].strip()


def build_message(payload: dict[str, Any]) -> str:
    status = _safe_text(payload.get("status"), 32).upper() or "ALERT"
    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        alerts = []
    lines = [f"[LogSentinel {status}] {len(alerts)} alert(s)"]
    for alert in alerts[:10]:
        if not isinstance(alert, dict):
            continue
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        annotations = (
            alert.get("annotations")
            if isinstance(alert.get("annotations"), dict)
            else {}
        )
        name = _safe_text(labels.get("alertname"), 120) or "unnamed-alert"
        severity = _safe_text(labels.get("severity"), 32)
        component = _safe_text(labels.get("component"), 64)
        summary = _safe_text(annotations.get("summary"), 300)
        detail = _safe_text(annotations.get("description"), 500)
        prefix = " ".join(part for part in (name, severity, component) if part)
        lines.append(f"- {prefix}: {summary or detail or 'see monitoring runbook'}")
    return "\n".join(lines)[:MAX_CONTENT]


def deliver(payload: dict[str, Any], destination: str) -> None:
    global _delivered, _failed
    url = validate_destination(destination)
    body = json.dumps(
        {"content": build_message(payload), "allowed_mentions": {"parse": []}},
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "LogSentinel-AlertRelay/1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError("operator provider rejected notification")
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError):
        with _lock:
            _failed += 1
        raise
    with _lock:
        _delivered += 1


class Handler(BaseHTTPRequestHandler):
    server_version = "LogSentinelAlertRelay/1"

    def _respond(
        self, status: int, body: bytes, content_type: str = "text/plain"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._respond(HTTPStatus.OK, b"ok\n")
            return
        if self.path == "/metrics":
            with _lock:
                delivered = _delivered
                failed = _failed
            body = (
                "# TYPE logsentinel_alert_relay_up gauge\n"
                "logsentinel_alert_relay_up 1\n"
                "# TYPE logsentinel_alert_relay_deliveries_total counter\n"
                f"logsentinel_alert_relay_deliveries_total {delivered}\n"
                "# TYPE logsentinel_alert_relay_delivery_failures_total counter\n"
                f"logsentinel_alert_relay_delivery_failures_total {failed}\n"
            ).encode("ascii")
            self._respond(HTTPStatus.OK, body, "text/plain; version=0.0.4")
            return
        self._respond(HTTPStatus.NOT_FOUND, b"not found\n")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/alert":
            self._respond(HTTPStatus.NOT_FOUND, b"not found\n")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise ValueError("invalid body length")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("alert payload must be an object")
            destination = os.environ["ALERT_WEBHOOK_URL"]
            deliver(payload, destination)
        except Exception:
            self._respond(HTTPStatus.BAD_GATEWAY, b"notification delivery failed\n")
            return
        self._respond(HTTPStatus.ACCEPTED, b"notification delivered\n")

    def log_message(self, format: str, *args: Any) -> None:
        # Do not log request bodies, destinations, or provider error payloads.
        return


def main() -> int:
    destination = os.environ.get("ALERT_WEBHOOK_URL", "")
    validate_destination(destination)
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

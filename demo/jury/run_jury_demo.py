#!/usr/bin/env python3
"""Send five labeled jury-demo logs through the real LogSentinel pipeline."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_API_URL = "https://138.2.152.189.sslip.io"
API_URL = os.getenv("LOGSENTINEL_API_URL", DEFAULT_API_URL).rstrip("/")
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "temporary-report" / "jury-demo" / "latest.json"


class ApiFailure(Exception):
    def __init__(self, operation: str, status: int):
        super().__init__(f"{operation} failed with HTTP {status}")
        self.operation = operation
        self.status = status


def request_json(
    method: str,
    path: str,
    *,
    payload: Any = None,
    headers: dict[str, str] | None = None,
    operation: str,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        API_URL + path, data=body, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        raise ApiFailure(operation, error.code) from None
    except (urllib.error.URLError, TimeoutError):
        raise ApiFailure(operation, 0) from None
    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiFailure(operation, status) from None
    if not isinstance(decoded, dict):
        raise ApiFailure(operation, status)
    return status, decoded


def write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def hidden_input(prompt: str, operation: str) -> str:
    """Read a secret only when a real terminal can keep input hidden."""
    if not sys.stdin.isatty():
        raise ApiFailure(operation + " (set the documented environment variable)", 0)
    return getpass.getpass(prompt).strip()


def make_log(run_id: str, index: int, scenario: str, level: str, detail: str) -> dict[str, Any]:
    marker = f"[jury-demo:{run_id}:{index}]"
    return {
        "event_id": f"jury-{run_id}-{index}",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "service_name": "jury-demo-api",
        "level": level,
        "message": f"{marker} {detail}",
        "metadata": {
            "jury_demo": True,
            "synthetic_sample": True,
            "demo_run": run_id,
            "scenario": scenario,
        },
    }


def post_logs(api_key: str, logs: list[dict[str, Any]]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    status, response = request_json(
        "POST",
        "/api/v1/ingest/bulk",
        payload=logs,
        headers={"X-API-Key": api_key, "X-Service-Name": "jury-demo-api"},
        operation="log ingestion",
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    expected = len(logs)
    if status != 202 or response.get("ingested_count") != expected:
        raise ApiFailure("log ingestion acceptance", status)
    return response, elapsed_ms


def verify_persistence(access_token: str, run_id: str, timeout_seconds: int = 60) -> int:
    expected_markers = {f"[jury-demo:{run_id}:{index}]" for index in range(1, 6)}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _, response = request_json(
            "GET",
            "/api/v1/logs/recent?limit=1000",
            headers={"Authorization": f"Bearer {access_token}"},
            operation="persisted-log lookup",
        )
        logs = response.get("logs")
        if not isinstance(logs, list):
            raise ApiFailure("persisted-log response validation", 200)
        found: set[str] = set()
        for row in logs:
            if not isinstance(row, dict):
                continue
            raw_message = row.get("raw_message")
            if isinstance(raw_message, str):
                found.update(marker for marker in expected_markers if marker in raw_message)
        if found == expected_markers:
            return len(found)
        time.sleep(2)
    return len(found)


def create_demo_key() -> tuple[str, str]:
    """Create a key with the existing password login flow; never display it."""
    email = input("LogSentinel account email: ").strip()
    password = hidden_input(
        "LogSentinel account password: ", "password input"
    )
    _, login = request_json(
        "POST",
        "/api/auth/login",
        payload={"email": email, "password": password},
        operation="account login",
    )
    access_token = login.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ApiFailure("account login response validation", 200)

    key_query = urllib.parse.urlencode(
        {"name": "jury-demo", "expires_in_days": "7"}
    )
    _, key_response = request_json(
        "POST",
        "/api/auth/api-key?" + key_query,
        headers={"Authorization": "Bearer " + access_token},
        operation="demonstration-key creation",
    )
    api_key = key_response.get("api_key")
    if not isinstance(api_key, str) or not api_key:
        raise ApiFailure("demonstration-key response validation", 200)
    return api_key, access_token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-key",
        action="store_true",
        help="Use the existing email/password login flow to create an in-memory demo key.",
    )
    return parser.parse_args()


def get_existing_key(create_key_requested: bool) -> tuple[str | None, str]:
    """Read an existing key without echoing it or accepting it on the CLI."""
    from_environment = os.getenv("LOGSENTINEL_INGEST_API_KEY", "").strip()
    if from_environment:
        return from_environment, "environment"
    if create_key_requested:
        return None, "create"
    entered = hidden_input(
        "LogSentinel ingestion API key (hidden; Enter cancels; rerun with --create-key to create): ",
        "ingestion key input",
    )
    if entered:
        return entered, "hidden_prompt"
    return None, "missing"


def get_owner_access_token(login_token: str | None) -> str | None:
    """Read the existing app bearer needed by the owner-scoped log lookup."""
    if login_token:
        return login_token
    from_environment = os.getenv("LOGSENTINEL_ACCESS_TOKEN", "").strip()
    if from_environment:
        return from_environment
    entered = hidden_input(
        "LogSentinel owner access token for persistence lookup (hidden; Enter to skip): ",
        "owner access token input",
    )
    return entered or None


def main() -> int:
    args = parse_args()
    run_id = secrets.token_hex(6)
    api_key: str | None = None
    access_token: str | None = None
    report: dict[str, Any] = {
        "run_id": run_id,
        "backend_origin": API_URL,
        "credential_mode": "not_selected",
        "key_creation": "not_started",
        "key_material_saved": False,
        "ingestion": "not_started",
        "accepted_logs": 0,
        "persisted_logs": 0,
        "latency_measurement": None,
        "scenarios": ["normal", "errors", "authentication-failures", "latency"],
        "application_visualization": "pending_signed_in_browser_check",
    }
    try:
        api_key, key_source = get_existing_key(args.create_key)
        if api_key:
            report["credential_mode"] = "existing_ingestion_key"
            report["key_creation"] = "skipped_existing_key"
            print("Using the supplied ingestion key; key creation and account login skipped.")
        elif key_source == "create":
            report["credential_mode"] = "create_demo_key"
            api_key, access_token = create_demo_key()
            report["key_creation"] = "pass"
        else:
            raise ApiFailure(
                "ingestion key input (set LOGSENTINEL_INGEST_API_KEY, enter it when prompted, or use --create-key)",
                0,
            )

        first_batch = [
            make_log(run_id, 1, "normal", "INFO", "normal request completed successfully"),
            make_log(run_id, 2, "normal", "INFO", "health check completed successfully"),
            make_log(run_id, 3, "errors", "ERROR", "test request returned HTTP 500"),
            make_log(
                run_id,
                4,
                "authentication-failures",
                "WARN",
                "test authentication was rejected with HTTP 401",
            ),
        ]
        first_response, measured_ms = post_logs(api_key, first_batch)
        latency_log = make_log(
            run_id,
            5,
            "latency",
            "INFO",
            "measured ingestion API round-trip latency",
        )
        latency_log["metadata"]["ingestion_api_round_trip_ms"] = measured_ms
        second_response, _ = post_logs(api_key, [latency_log])
        accepted = int(first_response["ingested_count"]) + int(
            second_response["ingested_count"]
        )
        report["ingestion"] = "pass"
        report["accepted_logs"] = accepted
        report["latency_measurement"] = {
            "kind": "client-observed-ingestion-api-round-trip-ms",
            "value": measured_ms,
        }
        print(f"Ingestion accepted {accepted}/5 logs (HTTP 202).")

        access_token = get_owner_access_token(access_token)
        if access_token:
            try:
                persisted = verify_persistence(access_token, run_id)
                report["persisted_logs"] = persisted
                report["persistence"] = "pass" if persisted == 5 else "pending_or_partial"
                print(f"Persisted log retrieval found {persisted}/5 demonstration records.")
            except ApiFailure as error:
                report["persistence"] = "unavailable"
                report["persistence_http_status"] = error.status
                print("Persisted-log retrieval could not be confirmed through the authorized API.")
        else:
            report["persistence"] = "blocked_missing_owner_access_token"
            print(
                "Ingestion succeeded, but persistence lookup needs an owner access token; "
                "the ingestion key cannot read dashboard logs."
            )

        write_report(report)
        print(f"Diagnostic report: {REPORT_PATH}")
        return 0 if accepted == 5 and report.get("persistence") == "pass" else 2
    except (ApiFailure, KeyboardInterrupt) as error:
        if isinstance(error, ApiFailure):
            report["failure"] = str(error)
            print(str(error), file=sys.stderr)
        else:
            report["failure"] = "cancelled_by_owner"
            print("Cancelled.", file=sys.stderr)
        write_report(report)
        print(f"Diagnostic report: {REPORT_PATH}", file=sys.stderr)
        return 1
    finally:
        api_key = None
        access_token = None


if __name__ == "__main__":
    raise SystemExit(main())

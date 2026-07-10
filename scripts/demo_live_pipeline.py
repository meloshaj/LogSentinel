"""Development/demo script only.

This script generates synthetic logs and sends them through the real
LogSentinel ingestion pipeline. It is not used by the application runtime.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from typing import Any


DEFAULT_API_URL = "http://localhost:8000"
SERVICES = ("auth-service", "payment-service", "order-service")


def get_api_url() -> str:
    return os.getenv("LOGSENTINEL_API_URL", DEFAULT_API_URL).strip().rstrip("/")


def get_api_key() -> str:
    api_key = os.getenv("INGEST_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set INGEST_API_KEY before running this demo.")
    return api_key


def generate_synthetic_logs(total: int = 64) -> list[dict[str, Any]]:
    """Generate synthetic service logs matching the /ingest-log LogEntry schema."""
    logs: list[dict[str, Any]] = []
    correlation_ids = [f"demo-trace-{index:03d}" for index in range(1, 9)]

    for index in range(total):
        service = SERVICES[index % len(SERVICES)]
        correlation_id = correlation_ids[(index // 3) % len(correlation_ids)]
        level = "INFO"
        message = _normal_message(service, index)

        if index % 13 in (7, 8):
            level = "WARNING"
            message = _warning_message(service, index)

        if 30 <= index < 42 or index % 17 == 0:
            level = "ERROR"
            message = _error_message(service, index)

        logs.append(
            {
                "service_name": service,
                "level": level,
                "message": message,
                "metadata": {
                    "trace_id": correlation_id,
                    "correlation_id": correlation_id,
                    "demo_sequence": index,
                },
            }
        )

    return logs


def build_ingest_batches(logs: list[dict[str, Any]], batch_size: int = 8) -> list[dict[str, Any]]:
    """Build /ingest-log payloads with 5-10 logs per request by default."""
    if batch_size < 5 or batch_size > 10:
        raise ValueError("batch_size must be between 5 and 10 for this demo")

    chunks = [logs[start : start + batch_size] for start in range(0, len(logs), batch_size)]
    if len(chunks) > 1 and 0 < len(chunks[-1]) < 5:
        needed = 5 - len(chunks[-1])
        chunks[-1] = chunks[-2][-needed:] + chunks[-1]
        chunks[-2] = chunks[-2][:-needed]

    batches: list[dict[str, Any]] = []
    for batch_logs in chunks:
        correlation_id = _correlation_id_for_batch(batch_logs)
        batches.append(
            {
                "source": "live-pipeline-demo",
                "environment": "local-demo",
                "correlation_id": correlation_id,
                "logs": batch_logs,
            }
        )

    return batches


def post_json(base_url: str, path: str, payload: dict[str, Any] | None = None, api_key: str | None = None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload or {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method="POST",
    )
    return send_json(request)


def get_json(base_url: str, path: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(f"{base_url}{path}", method="GET")
    return send_json(request)


def send_json(request: urllib.request.Request) -> tuple[int, dict[str, Any]]:
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def send_batches(base_url: str, api_key: str, batches: list[dict[str, Any]], delay_seconds: float = 0.4) -> None:
    for index, payload in enumerate(batches, start=1):
        try:
            status_code, response = post_json(base_url, "/ingest-log", payload, api_key=api_key)
            print(
                f"batch {index}/{len(batches)}: sent={len(payload['logs'])} "
                f"status={status_code} accepted={response.get('accepted')}"
            )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            print(f"batch {index}/{len(batches)}: HTTP {exc.code} {body}")
        except urllib.error.URLError as exc:
            raise SystemExit(f"Could not reach backend at {base_url}: {exc}") from exc

        time.sleep(delay_seconds)


def trigger_feature_extraction(base_url: str) -> None:
    try:
        status_code, response = post_json(base_url, "/features/extract")
        print(f"feature extraction trigger: status={status_code} features_extracted={response.get('features_extracted')}")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        print(f"warning: could not trigger feature extraction: {exc}")


def print_verification_summary(base_url: str) -> None:
    recent_logs = _safe_get(base_url, "/drain3/recent?limit=100", "recent parsed logs")
    recent_features = _safe_get(base_url, "/features/recent?limit=20", "recent feature windows")

    parsed_logs = recent_logs.get("logs", []) if isinstance(recent_logs, dict) else []
    features = recent_features.get("features", []) if isinstance(recent_features, dict) else []
    services = sorted({log.get("service") for log in parsed_logs if log.get("service")})
    error_count = sum(1 for log in parsed_logs if str(log.get("level", "")).upper() == "ERROR")

    print("\nVerification summary")
    print(f"recent parsed logs count: {len(parsed_logs)}")
    print(f"recent feature windows count: {len(features)}")
    print(f"services observed: {', '.join(services) if services else 'none'}")
    print(f"error count: {error_count}")


def main() -> None:
    base_url = get_api_url()
    api_key = get_api_key()
    logs = generate_synthetic_logs()
    batches = build_ingest_batches(logs)

    print(f"Sending {len(logs)} synthetic logs to {base_url}/ingest-log")
    print("Open the React Logs page to watch WebSocket telemetry update live.")
    send_batches(base_url, api_key, batches)
    trigger_feature_extraction(base_url)
    print_verification_summary(base_url)


def _safe_get(base_url: str, path: str, label: str) -> dict[str, Any]:
    try:
        _, response = get_json(base_url, path)
        return response
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        print(f"warning: could not query {label}: {exc}")
        return {}


def _correlation_id_for_batch(logs: list[dict[str, Any]]) -> str:
    ids = [
        str(log.get("metadata", {}).get("correlation_id"))
        for log in logs
        if log.get("metadata", {}).get("correlation_id")
    ]
    return Counter(ids).most_common(1)[0][0] if ids else "demo-trace"


def _normal_message(service: str, index: int) -> str:
    if service == "auth-service":
        return f"login succeeded for user-{index % 12} from 10.10.0.{index % 20}"
    if service == "payment-service":
        return f"payment authorization approved for order-{5000 + index}"
    return f"order {7000 + index} moved to fulfillment queue"


def _warning_message(service: str, index: int) -> str:
    if service == "auth-service":
        return f"token refresh latency high for user-{index % 12}: {250 + index}ms"
    if service == "payment-service":
        return f"payment provider retry scheduled for transaction-{9000 + index}"
    return f"inventory reservation slow for sku-{100 + index}: retrying"


def _error_message(service: str, index: int) -> str:
    if service == "auth-service":
        return f"auth timeout while validating session user-{index % 12} after 5000ms"
    if service == "payment-service":
        return f"payment timeout contacting gateway for transaction-{9000 + index} after 5000ms"
    return f"order timeout reserving inventory for order-{7000 + index} after 5000ms"


if __name__ == "__main__":
    main()

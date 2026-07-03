"""End-to-end Drain3 ingestion demo for a locally running backend."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


BASE_URL = "http://localhost:8000"


def post_json(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return send(request)


def get_json(path: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{BASE_URL}{path}", method="GET")
    return send(request)


def send(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise SystemExit(f"HTTP {exc.code} from {request.full_url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach backend at {BASE_URL}: {exc}") from exc


def sample_payloads() -> list[dict[str, Any]]:
    return [
        {
            "source": "demo-script",
            "environment": "local",
            "correlation_id": f"trace-auth-{index}",
            "logs": [
                {
                    "service_name": "auth-service",
                    "level": "info",
                    "message": f"user user-{index} logged in from 192.168.1.{index}",
                    "metadata": {"trace_id": f"trace-auth-{index}"},
                }
            ],
        }
        for index in range(1, 5)
    ] + [
        {
            "source": "demo-script",
            "environment": "local",
            "correlation_id": f"trace-payment-{index}",
            "logs": [
                {
                    "service_name": "payment-service",
                    "level": "error",
                    "message": f"payment request {1000 + index} failed to connect to 10.0.0.{index} on port 5432",
                    "metadata": {"trace_id": f"trace-payment-{index}"},
                }
            ],
        }
        for index in range(1, 5)
    ] + [
        {
            "source": "demo-script",
            "environment": "local",
            "correlation_id": f"trace-order-{index}",
            "logs": [
                {
                    "service_name": "order-service",
                    "level": "warning",
                    "message": f"order {2000 + index} inventory reservation timed out after {index + 2}s",
                    "metadata": {"trace_id": f"trace-order-{index}"},
                }
            ],
        }
        for index in range(1, 5)
    ] + [
        {
            "source": "demo-script",
            "environment": "local",
            "correlation_id": f"trace-notification-{index}",
            "logs": [
                {
                    "service_name": "notification-service",
                    "level": "info",
                    "message": f"email notification {3000 + index} queued for user-{index}@example.com",
                    "metadata": {"trace_id": f"trace-notification-{index}"},
                }
            ],
        }
        for index in range(1, 5)
    ]


def print_json(title: str, payload: dict[str, Any]) -> None:
    print(f"\n== {title} ==")
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> None:
    print(f"Sending Drain3 demo logs to {BASE_URL}")

    for payload in sample_payloads():
        response = post_json("/ingest-log", payload)
        service = payload["logs"][0]["service_name"]
        print(f"accepted {service}: accepted={response.get('accepted')} queue_size={response.get('queue_size')}")

    print("\nWaiting 6 seconds so the 5-second periodic flush can run...")
    time.sleep(6)

    print_json("Drain3 Stats After Periodic Flush", get_json("/drain3/stats"))
    print_json("Drain3 Templates", get_json("/drain3/templates"))
    print_json("Final Safety Flush", post_json("/drain3/flush"))
    print_json("Final Drain3 Stats", get_json("/drain3/stats"))


if __name__ == "__main__":
    main()

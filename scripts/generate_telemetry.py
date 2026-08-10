#!/usr/bin/env python3
"""Asynchronous synthetic telemetry generator for LogSentinel stress-testing.

Generates realistic baseline traffic across four simulated microservices,
then injects targeted anomaly scenarios designed to exercise:
  - Drain3 dynamic template extraction & novelty scoring
  - Isolation Forest anomaly detection baseline training
  - RCA tracking-loop temporal sequence correlation

Zero external dependencies — uses only the Python 3.11+ standard library.

Usage:
    python scripts/generate_telemetry.py
    python scripts/generate_telemetry.py --base-url http://192.168.1.5:8000 --rate 8
    INGEST_API_KEY=my-key python scripts/generate_telemetry.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

SERVICES = ["api-gateway", "auth-service", "payment-service", "postgres-db"]

LOG_LEVELS = ["INFO", "WARN", "ERROR", "CRITICAL"]

HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]

API_GATEWAY_ENDPOINTS = [
    "/api/v1/users",
    "/api/v1/orders",
    "/api/v1/payments",
    "/api/v1/products",
    "/api/v1/health",
    "/api/v1/analytics",
    "/api/v1/notifications",
    "/api/v1/billing/invoices",
]

AUTH_ENDPOINTS = [
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh-token",
    "/api/auth/verify-email",
    "/api/auth/logout",
]

PAYMENT_OPERATIONS = [
    "charge",
    "refund",
    "authorize",
    "capture",
    "void",
    "payout",
]

SQL_OPERATIONS = [
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
]

SQL_TABLES = [
    "users",
    "orders",
    "payments",
    "sessions",
    "audit_log",
    "products",
    "invoices",
]


# ─────────────────────────────────────────────────────────────────────
# Baseline log message generators (per-service)
# ─────────────────────────────────────────────────────────────────────


def _baseline_api_gateway() -> dict[str, Any]:
    """Produce a realistic api-gateway log entry."""
    method = random.choice(HTTP_METHODS)
    endpoint = random.choice(API_GATEWAY_ENDPOINTS)
    status_code = random.choices([200, 201, 204, 301, 400, 404], weights=[60, 10, 5, 3, 5, 2])[0]
    latency_ms = round(random.gauss(45, 18), 1)
    if latency_ms < 1:
        latency_ms = 1.0
    client_ip = f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"
    request_id = f"req-{random.randint(100000, 999999)}"
    message = (
        f'{method} {endpoint} completed with status {status_code} '
        f'in {latency_ms}ms from {client_ip} request_id={request_id}'
    )
    return {
        "service_name": "api-gateway",
        "level": "INFO" if status_code < 400 else "WARN",
        "message": message,
        "metadata": {
            "method": method,
            "endpoint": endpoint,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "client_ip": client_ip,
            "request_id": request_id,
        },
    }


def _baseline_auth_service() -> dict[str, Any]:
    """Produce a realistic auth-service log entry."""
    endpoint = random.choice(AUTH_ENDPOINTS)
    user_id = f"user-{random.randint(1000, 9999)}"
    session_id = f"sess-{random.randint(100000, 999999)}"
    scenarios = [
        (
            "INFO",
            f"Authentication successful for {user_id} via {endpoint} session={session_id}",
        ),
        (
            "INFO",
            f"Token refreshed for {user_id} session={session_id} ttl=3600s",
        ),
        (
            "INFO",
            f"User {user_id} logged out cleanly session={session_id}",
        ),
        (
            "WARN",
            f"Password attempt failed for {user_id} via {endpoint} attempts=2 remaining=3",
        ),
    ]
    level, message = random.choices(scenarios, weights=[50, 20, 15, 5])[0]
    return {
        "service_name": "auth-service",
        "level": level,
        "message": message,
        "metadata": {"user_id": user_id, "endpoint": endpoint, "session_id": session_id},
    }


def _baseline_payment_service() -> dict[str, Any]:
    """Produce a realistic payment-service log entry."""
    operation = random.choice(PAYMENT_OPERATIONS)
    txn_id = f"txn-{random.randint(100000, 999999)}"
    amount = round(random.uniform(5.00, 2500.00), 2)
    currency = random.choice(["USD", "EUR", "GBP"])
    latency_ms = round(random.gauss(120, 40), 1)
    if latency_ms < 5:
        latency_ms = 5.0
    message = (
        f"Payment {operation} completed for {txn_id} "
        f"amount={amount} {currency} processing_time={latency_ms}ms"
    )
    return {
        "service_name": "payment-service",
        "level": "INFO",
        "message": message,
        "metadata": {
            "operation": operation,
            "txn_id": txn_id,
            "amount": amount,
            "currency": currency,
            "latency_ms": latency_ms,
        },
    }


def _baseline_postgres_db() -> dict[str, Any]:
    """Produce a realistic postgres-db log entry."""
    operation = random.choice(SQL_OPERATIONS)
    table = random.choice(SQL_TABLES)
    rows_affected = random.randint(0, 500)
    duration_ms = round(random.gauss(8, 4), 2)
    if duration_ms < 0.1:
        duration_ms = 0.1
    conn_id = random.randint(100, 999)
    message = (
        f"{operation} on {table} affected {rows_affected} rows "
        f"duration={duration_ms}ms connection_id={conn_id}"
    )
    return {
        "service_name": "postgres-db",
        "level": "INFO",
        "message": message,
        "metadata": {
            "operation": operation,
            "table": table,
            "rows_affected": rows_affected,
            "duration_ms": duration_ms,
            "connection_id": conn_id,
        },
    }


BASELINE_GENERATORS = [
    _baseline_api_gateway,
    _baseline_auth_service,
    _baseline_payment_service,
    _baseline_postgres_db,
]


def generate_baseline_entry() -> dict[str, Any]:
    """Pick a random service and return a baseline log entry."""
    generator = random.choice(BASELINE_GENERATORS)
    entry = generator()
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    return entry


# ─────────────────────────────────────────────────────────────────────
# Anomaly scenario generators
# ─────────────────────────────────────────────────────────────────────


def scenario_a_cascading_db_exhaustion() -> list[dict[str, Any]]:
    """Scenario A: Cascading Database Exhaustion.

    Phase 1 — postgres-db slow query lock waits
    Phase 2 — payment-service connection pool timeouts (delayed)
    Phase 3 — api-gateway HTTP 504 Gateway Timeouts (delayed)

    Delays between phases test RCA temporal sequence detection.
    """
    entries: list[dict[str, Any]] = []
    base_time = time.time()

    # Phase 1: postgres-db lock contention (t+0s to t+3s)
    for i in range(8):
        lock_wait_ms = round(random.uniform(5000, 30000), 1)
        blocked_pid = random.randint(200, 400)
        blocking_pid = random.randint(100, 199)
        table = random.choice(["payments", "orders", "invoices"])
        entries.append({
            "service_name": "postgres-db",
            "level": "ERROR",
            "message": (
                f"Lock wait timeout exceeded on {table}: "
                f"pid={blocked_pid} blocked_by={blocking_pid} "
                f"wait_time={lock_wait_ms}ms lock_type=RowExclusiveLock "
                f"query=UPDATE {table} SET status='processing' WHERE id={random.randint(10000,99999)}"
            ),
            "metadata": {
                "blocked_pid": blocked_pid,
                "blocking_pid": blocking_pid,
                "lock_wait_ms": lock_wait_ms,
                "table": table,
                "phase": "db_lock_contention",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_delay": i * 0.4,
        })

    # Phase 2: payment-service pool exhaustion (t+4s to t+6s)
    for i in range(6):
        entries.append({
            "service_name": "payment-service",
            "level": "CRITICAL",
            "message": (
                f"Database connection pool exhausted: "
                f"active={50 + i} idle=0 max_pool_size=50 "
                f"pending_acquisitions={12 + i * 3} "
                f"oldest_connection_age={random.randint(120, 600)}s "
                f"txn-{random.randint(100000, 999999)} operation=charge FAILED"
            ),
            "metadata": {
                "active_connections": 50 + i,
                "max_pool_size": 50,
                "pending_acquisitions": 12 + i * 3,
                "phase": "pool_exhaustion",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_delay": 4.0 + i * 0.35,
        })

    # Phase 3: api-gateway 504s (t+7s to t+9s)
    for i in range(6):
        endpoint = random.choice(["/api/v1/payments", "/api/v1/orders"])
        entries.append({
            "service_name": "api-gateway",
            "level": "ERROR",
            "message": (
                f"POST {endpoint} upstream timeout after 30000ms: "
                f"HTTP 504 Gateway Timeout request_id=req-{random.randint(100000,999999)} "
                f"upstream=payment-service retry_count={random.randint(0,3)}"
            ),
            "metadata": {
                "status_code": 504,
                "upstream": "payment-service",
                "timeout_ms": 30000,
                "endpoint": endpoint,
                "phase": "gateway_timeout",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_delay": 7.0 + i * 0.3,
        })

    return entries


def scenario_b_novel_exception() -> list[dict[str, Any]]:
    """Scenario B: Novel multi-line PydanticValidationError on auth-service.

    Injects an unhandled exception stack trace to test Drain3 dynamic
    template extraction and novelty score spikes.
    """
    stack_trace = (
        "Traceback (most recent call last):\n"
        '  File "/app/auth-service/handlers/registration.py", line 142, in handle_registration\n'
        "    validated = RegistrationSchema(**request_body)\n"
        '  File "/app/.venv/lib/python3.11/site-packages/pydantic/main.py", line 164, in __init__\n'
        "    __pydantic_self__.__pydantic_validator__.validate_python(data, self_init=__pydantic_self__)\n"
        "pydantic_core._pydantic_core.ValidationError: 3 validation errors for RegistrationSchema\n"
        "email\n"
        "  value is not a valid email address: The email address is not valid. "
        "It must have exactly one @-sign. [type=value_error, input_value='not-an-email', input_type=str]\n"
        "password\n"
        "  String should have at least 12 characters [type=string_too_short, input_value='short', input_type=str]\n"
        "organization_id\n"
        "  Input should be a valid UUID [type=uuid_parsing, input_value='definitely-not-a-uuid', input_type=str]\n"
        "\n"
        "During handling of the above exception, another exception occurred:\n"
        "\n"
        "Traceback (most recent call last):\n"
        '  File "/app/auth-service/middleware/error_handler.py", line 38, in __call__\n'
        "    response = await call_next(request)\n"
        '  File "/app/auth-service/handlers/registration.py", line 148, in handle_registration\n'
        "    raise HTTPException(status_code=422, detail=str(e))\n"
        "fastapi.exceptions.HTTPException: 422 Unprocessable Entity"
    )

    entries: list[dict[str, Any]] = []

    # Preceding normal log right before the explosion
    entries.append({
        "service_name": "auth-service",
        "level": "INFO",
        "message": f"POST /api/auth/register received from 10.0.12.{random.randint(1,254)} request_id=req-{random.randint(100000,999999)}",
        "metadata": {"phase": "novel_exception_preamble"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "_delay": 0.0,
    })

    # The unhandled exception
    entries.append({
        "service_name": "auth-service",
        "level": "CRITICAL",
        "message": f"Unhandled PydanticValidationError in registration handler:\n{stack_trace}",
        "metadata": {
            "exception_type": "pydantic_core._pydantic_core.ValidationError",
            "validation_error_count": 3,
            "handler": "handle_registration",
            "phase": "novel_exception",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "_delay": 0.3,
    })

    # Follow-up error logs from cascading middleware failures
    for i in range(3):
        entries.append({
            "service_name": "auth-service",
            "level": "ERROR",
            "message": (
                f"Error response middleware caught unhandled exception: "
                f"PydanticValidationError correlation_id=corr-{random.randint(10000,99999)} "
                f"request_path=/api/auth/register client_ip=10.0.12.{random.randint(1,254)}"
            ),
            "metadata": {
                "exception_type": "PydanticValidationError",
                "phase": "novel_exception_cascade",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_delay": 0.6 + i * 0.15,
        })

    return entries


def scenario_c_auth_burst() -> list[dict[str, Any]]:
    """Scenario C: High-frequency auth failure burst.

    Injects 28 rapid HTTP 429/401 rate-limit failure logs on
    POST /api/auth/login within approximately 1 second.
    """
    entries: list[dict[str, Any]] = []
    attacker_ips = [
        f"198.51.100.{random.randint(1, 254)}" for _ in range(4)
    ]

    for i in range(28):
        ip = random.choice(attacker_ips)
        status_code = random.choices([429, 401], weights=[65, 35])[0]
        user_target = f"admin@company-{random.randint(1,5)}.com"

        if status_code == 429:
            message = (
                f"Rate limit exceeded on POST /api/auth/login from {ip}: "
                f"HTTP 429 Too Many Requests retry_after=60s "
                f"user_attempt={user_target} window_hits={random.randint(20, 50)}"
            )
        else:
            message = (
                f"Authentication failed on POST /api/auth/login from {ip}: "
                f"HTTP 401 Unauthorized user={user_target} "
                f"reason=invalid_credentials attempt={random.randint(3, 10)}"
            )

        entries.append({
            "service_name": "auth-service",
            "level": "WARN" if status_code == 429 else "ERROR",
            "message": message,
            "metadata": {
                "status_code": status_code,
                "client_ip": ip,
                "user_attempt": user_target,
                "endpoint": "/api/auth/login",
                "phase": "auth_burst",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_delay": i * 0.035,  # ~28 logs in ~1 second
        })

    return entries


# ─────────────────────────────────────────────────────────────────────
# HTTP transport (stdlib only, non-blocking via thread executor)
# ─────────────────────────────────────────────────────────────────────


class TelemetrySender:
    """Non-blocking HTTP sender using asyncio and urllib."""

    def __init__(self, base_url: str, api_key: str, source: str = "telemetry-generator"):
        self.ingest_url = f"{base_url.rstrip('/')}/ingest-log"
        self.api_key = api_key
        self.source = source
        self._sent = 0
        self._failed = 0
        self._retries = 0

    def _post_sync(self, payload: dict[str, Any]) -> tuple[bool, int]:
        """Synchronous HTTP POST (run in executor for async)."""
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.ingest_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return True, resp.status
        except urllib.error.HTTPError as exc:
            return False, exc.code
        except (urllib.error.URLError, OSError):
            return False, 0

    async def send_entry(self, entry: dict[str, Any], max_retries: int = 2) -> bool:
        """Send a single log entry wrapped in IngestPayload with retry."""
        # Strip internal scheduling key
        clean = {k: v for k, v in entry.items() if not k.startswith("_")}
        payload = {
            "source": self.source,
            "environment": "stress-test",
            "logs": [clean],
            "correlation_id": f"synth-{int(time.time() * 1000)}",
        }

        loop = asyncio.get_running_loop()
        for attempt in range(max_retries + 1):
            ok, status = await loop.run_in_executor(None, self._post_sync, payload)
            if ok:
                self._sent += 1
                return True
            if status in (429, 503) and attempt < max_retries:
                self._retries += 1
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            break

        self._failed += 1
        return False

    async def send_batch(self, entries: list[dict[str, Any]]) -> int:
        """Send a batch of log entries wrapped in a single IngestPayload."""
        clean_entries = [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]
        payload = {
            "source": self.source,
            "environment": "stress-test",
            "logs": clean_entries,
            "correlation_id": f"synth-batch-{int(time.time() * 1000)}",
        }

        loop = asyncio.get_running_loop()
        ok, status = await loop.run_in_executor(None, self._post_sync, payload)
        if ok:
            self._sent += len(clean_entries)
            return len(clean_entries)
        self._failed += len(clean_entries)
        return 0

    @property
    def stats(self) -> dict[str, int]:
        return {"sent": self._sent, "failed": self._failed, "retries": self._retries}


# ─────────────────────────────────────────────────────────────────────
# Console logging helpers
# ─────────────────────────────────────────────────────────────────────

BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def log_phase(phase: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"{CYAN}[{_ts()}]{RESET} {BOLD}{MAGENTA}▸ {phase}{RESET}{suffix}")


def log_info(msg: str) -> None:
    print(f"{CYAN}[{_ts()}]{RESET} {GREEN}✓{RESET} {msg}")


def log_warn(msg: str) -> None:
    print(f"{CYAN}[{_ts()}]{RESET} {YELLOW}⚠{RESET} {msg}")


def log_error(msg: str) -> None:
    print(f"{CYAN}[{_ts()}]{RESET} {RED}✗{RESET} {msg}")


def log_stat(label: str, value: Any) -> None:
    print(f"  {BLUE}│{RESET} {label}: {BOLD}{value}{RESET}")


# ─────────────────────────────────────────────────────────────────────
# Main execution flow
# ─────────────────────────────────────────────────────────────────────


async def run_baseline(sender: TelemetrySender, duration_seconds: float, rate: float) -> int:
    """Generate and send baseline steady-state traffic for a given duration."""
    interval = 1.0 / rate
    end_time = time.monotonic() + duration_seconds
    count = 0
    while time.monotonic() < end_time:
        entry = generate_baseline_entry()
        await sender.send_entry(entry)
        count += 1
        elapsed = time.monotonic()
        if elapsed < end_time:
            await asyncio.sleep(interval)
    return count


async def run_scenario(
    sender: TelemetrySender,
    name: str,
    entries: list[dict[str, Any]],
) -> int:
    """Send scenario entries respecting their _delay offsets."""
    log_phase(f"Scenario {name}", f"injecting {len(entries)} anomaly events")
    sent = 0
    start = time.monotonic()
    for entry in entries:
        target_delay = entry.get("_delay", 0.0)
        elapsed = time.monotonic() - start
        if target_delay > elapsed:
            await asyncio.sleep(target_delay - elapsed)
        ok = await sender.send_entry(entry)
        if ok:
            sent += 1
    log_info(f"Scenario {name} complete: {sent}/{len(entries)} events delivered")
    return sent


async def run_cooldown(sender: TelemetrySender, duration: float, rate: float) -> int:
    """Short cooldown period with reduced baseline traffic."""
    log_phase("Cooldown", f"{duration}s at {rate:.0f} logs/sec")
    return await run_baseline(sender, duration, rate)


async def main_flow(args: argparse.Namespace) -> None:
    """Orchestrate: warm-up → A → cooldown → B → cooldown → C → cooldown → report."""
    api_key = args.api_key or os.getenv("INGEST_API_KEY", "")
    if not api_key:
        log_error("No API key provided. Set --api-key or INGEST_API_KEY env var.")
        sys.exit(1)

    sender = TelemetrySender(base_url=args.base_url, api_key=api_key)
    rate = args.rate
    total_start = time.monotonic()

    print()
    print(f"{BOLD}{'═' * 64}{RESET}")
    print(f"{BOLD}{MAGENTA}  LogSentinel Synthetic Telemetry Generator{RESET}")
    print(f"{BOLD}{'═' * 64}{RESET}")
    log_stat("Target", sender.ingest_url)
    log_stat("Baseline Rate", f"{rate} logs/sec")
    log_stat("API Key", f"{api_key[:4]}{'*' * (len(api_key) - 4)}")
    print()

    # Phase 1: Warm-up baseline (15s)
    log_phase("Warm-up Baseline", f"15s at {rate} logs/sec — training Isolation Forest baseline")
    warmup_count = await run_baseline(sender, 15.0, rate)
    log_info(f"Warm-up complete: {warmup_count} baseline events sent")
    print()

    # Phase 2: Scenario A — Cascading Database Exhaustion
    scenario_a_entries = scenario_a_cascading_db_exhaustion()
    a_count = await run_scenario(sender, "A (Cascading DB Exhaustion)", scenario_a_entries)
    print()

    # Cooldown
    cd1 = await run_cooldown(sender, 5.0, rate)
    print()

    # Phase 3: Scenario B — Novel Exception Parsing
    scenario_b_entries = scenario_b_novel_exception()
    b_count = await run_scenario(sender, "B (Novel Exception Parsing)", scenario_b_entries)
    print()

    # Cooldown
    cd2 = await run_cooldown(sender, 5.0, rate)
    print()

    # Phase 4: Scenario C — High-Frequency Auth Burst
    scenario_c_entries = scenario_c_auth_burst()
    c_count = await run_scenario(sender, "C (High-Frequency Auth Burst)", scenario_c_entries)
    print()

    # Phase 5: Final cooldown baseline (15s)
    log_phase("Final Cooldown Baseline", f"15s at {rate} logs/sec")
    final_count = await run_baseline(sender, 15.0, rate)
    log_info(f"Final cooldown complete: {final_count} baseline events sent")
    print()

    # ─── Summary Report ────────────────────────────────────────────
    total_elapsed = time.monotonic() - total_start
    stats = sender.stats

    print(f"{BOLD}{'═' * 64}{RESET}")
    print(f"{BOLD}{GREEN}  Execution Summary{RESET}")
    print(f"{BOLD}{'═' * 64}{RESET}")
    log_stat("Total Duration", f"{total_elapsed:.1f}s")
    log_stat("Total Events Sent", stats["sent"])
    log_stat("Failed Deliveries", stats["failed"])
    log_stat("Retried Requests", stats["retries"])
    print(f"  {BLUE}│{RESET}")
    log_stat("Warm-up Baseline", f"{warmup_count} events")
    log_stat("Scenario A (DB Cascade)", f"{a_count} anomaly events")
    log_stat("Cooldown 1", f"{cd1} events")
    log_stat("Scenario B (Novel Exception)", f"{b_count} anomaly events")
    log_stat("Cooldown 2", f"{cd2} events")
    log_stat("Scenario C (Auth Burst)", f"{c_count} anomaly events")
    log_stat("Final Cooldown", f"{final_count} events")
    print(f"{BOLD}{'═' * 64}{RESET}")

    if stats["failed"] > 0:
        log_warn(f"{stats['failed']} events failed to deliver — check backend health")
    else:
        log_info("All events delivered successfully ✨")
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LogSentinel Synthetic Telemetry Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/generate_telemetry.py\n"
            "  python scripts/generate_telemetry.py --base-url http://192.168.1.5:8000 --rate 8\n"
            "  INGEST_API_KEY=my-key python scripts/generate_telemetry.py\n"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("TELEMETRY_BASE_URL", "http://localhost:8000"),
        help="Backend base URL (default: http://localhost:8000 or TELEMETRY_BASE_URL env)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("INGEST_API_KEY", ""),
        help="Ingestion API key (default: INGEST_API_KEY env var)",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=float(os.getenv("TELEMETRY_RATE", "7")),
        help="Baseline log generation rate in logs/sec (default: 7)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(main_flow(args))
    except KeyboardInterrupt:
        print(f"\n{YELLOW}⚠ Interrupted by user — exiting cleanly.{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()

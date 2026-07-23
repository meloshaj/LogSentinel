"""Realistic log template library with dynamic variable interpolation.

Each service type (gateway, auth, order, payment, database, generic) has a
curated set of log templates.  Templates use Python ``str.format`` placeholders
that are resolved at generation time with domain-appropriate random values.

The public API is ``render_log(service_type, level, rng)`` which returns a
``(message, raw, metadata)`` triple ready for ``LogEntry`` construction.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Random-value generators (deterministic when seeded via the caller's RNG)
# ---------------------------------------------------------------------------


def _random_ipv4(rng: random.Random) -> str:
    """Return a random RFC-1918-ish or public IPv4 address."""
    first = rng.choice([10, 172, 192, 203])
    return f"{first}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def _random_user_id(rng: random.Random) -> str:
    return f"usr_{rng.randint(100000, 999999)}"


def _random_order_id(rng: random.Random) -> str:
    return f"ORD-{rng.randint(2024000000, 2026999999)}"


def _random_txn_id(rng: random.Random) -> str:
    return f"txn_{uuid.UUID(int=rng.getrandbits(128)).hex[:16]}"


def _random_http_method(rng: random.Random) -> str:
    return rng.choice(["GET", "POST", "PUT", "DELETE", "PATCH"])


def _random_path(rng: random.Random) -> str:
    base = rng.choice([
        "/api/v1/orders",
        "/api/v1/users",
        "/api/v1/products",
        "/api/v1/cart",
        "/api/v1/checkout",
        "/api/v2/inventory",
        "/api/v1/payments",
        "/healthz",
        "/api/v1/auth/token",
        "/api/v1/auth/refresh",
    ])
    if rng.random() < 0.4:
        base += f"/{rng.randint(1, 50000)}"
    return base


def _random_status_for_level(level: str, rng: random.Random) -> int:
    if level == "error":
        return rng.choice([500, 502, 503, 504, 422, 429])
    if level == "warning":
        return rng.choice([400, 401, 403, 404, 408, 429])
    return rng.choice([200, 200, 200, 201, 204, 301, 304])


def _random_duration_ms(base: float, jitter: float, rng: random.Random) -> float:
    return round(max(0.1, base + rng.uniform(-jitter, jitter)), 2)


def _random_sql_table(rng: random.Random) -> str:
    return rng.choice([
        "orders", "order_items", "users", "products",
        "inventory", "payments", "sessions", "audit_log",
    ])


def _random_jwt_claim(rng: random.Random) -> str:
    return rng.choice(["exp", "iss", "aud", "sub", "iat", "nbf"])


# ---------------------------------------------------------------------------
# Template registries — keyed by (service_type, level)
# ---------------------------------------------------------------------------
# Each template is a tuple of (message_fmt, raw_fmt, metadata_factory).
# message_fmt  → the structured `message` field.
# raw_fmt      → the `raw` log line (realistic syslog / access-log style).
# metadata_factory → callable(vars) → dict  for the `metadata` field.

_TemplateEntry = tuple[str, str, Any]

# ---- Gateway templates ----------------------------------------------------

_GATEWAY_INFO: list[_TemplateEntry] = [
    (
        "{method} {path} completed in {duration_ms}ms with status {status}",
        '{client_ip} - - [{ts}] "{method} {path} HTTP/1.1" {status} {bytes} {duration_ms}ms',
        lambda v: {"client_ip": v["client_ip"], "method": v["method"],
                    "path": v["path"], "status": v["status"],
                    "duration_ms": v["duration_ms"], "bytes": v["bytes"]},
    ),
    (
        "Rate limit check passed for {client_ip} on {path}",
        '{ts} INFO api-gateway: rate_limit_check client_ip={client_ip} path={path} result=PASS remaining={remaining}',
        lambda v: {"client_ip": v["client_ip"], "path": v["path"],
                    "remaining": v["remaining"]},
    ),
    (
        "Upstream health check succeeded for {upstream}",
        '{ts} INFO api-gateway: health_check upstream={upstream} latency={duration_ms}ms status=healthy',
        lambda v: {"upstream": v["upstream"], "duration_ms": v["duration_ms"]},
    ),
]

_GATEWAY_WARNING: list[_TemplateEntry] = [
    (
        "Upstream {upstream} responded with status {status} in {duration_ms}ms",
        '{ts} WARN api-gateway: upstream_degraded upstream={upstream} status={status} latency={duration_ms}ms',
        lambda v: {"upstream": v["upstream"], "status": v["status"],
                    "duration_ms": v["duration_ms"]},
    ),
    (
        "Rate limit approaching threshold for {client_ip}: {remaining} requests remaining",
        '{ts} WARN api-gateway: rate_limit_warning client_ip={client_ip} remaining={remaining}',
        lambda v: {"client_ip": v["client_ip"], "remaining": v["remaining"]},
    ),
]

_GATEWAY_ERROR: list[_TemplateEntry] = [
    (
        "Circuit breaker OPEN for upstream {upstream} after {failures} consecutive failures",
        '{ts} ERROR api-gateway: circuit_breaker_open upstream={upstream} failures={failures}',
        lambda v: {"upstream": v["upstream"], "failures": v["failures"]},
    ),
    (
        "Gateway timeout: upstream {upstream} did not respond within {duration_ms}ms",
        '{ts} ERROR api-gateway: gateway_timeout upstream={upstream} timeout={duration_ms}ms',
        lambda v: {"upstream": v["upstream"], "duration_ms": v["duration_ms"]},
    ),
]

# ---- Auth templates -------------------------------------------------------

_AUTH_INFO: list[_TemplateEntry] = [
    (
        "JWT token issued for user {user_id} with expiry {expiry_s}s",
        '{ts} INFO auth-service: token_issued user_id={user_id} expiry={expiry_s}s client_ip={client_ip}',
        lambda v: {"user_id": v["user_id"], "expiry_s": v["expiry_s"],
                    "client_ip": v["client_ip"]},
    ),
    (
        "User {user_id} authenticated successfully via {auth_method}",
        '{ts} INFO auth-service: auth_success user_id={user_id} method={auth_method} ip={client_ip}',
        lambda v: {"user_id": v["user_id"], "auth_method": v["auth_method"],
                    "client_ip": v["client_ip"]},
    ),
    (
        "Token refreshed for user {user_id}",
        '{ts} INFO auth-service: token_refresh user_id={user_id} new_expiry={expiry_s}s',
        lambda v: {"user_id": v["user_id"], "expiry_s": v["expiry_s"]},
    ),
]

_AUTH_WARNING: list[_TemplateEntry] = [
    (
        "Failed login attempt for user {user_id} from {client_ip}",
        '{ts} WARN auth-service: auth_failed user_id={user_id} reason=invalid_credentials ip={client_ip} attempts={attempts}',
        lambda v: {"user_id": v["user_id"], "client_ip": v["client_ip"],
                    "attempts": v["attempts"]},
    ),
    (
        "JWT claim {jwt_claim} validation warning for user {user_id}",
        '{ts} WARN auth-service: jwt_claim_warning user_id={user_id} claim={jwt_claim} reason=near_expiry',
        lambda v: {"user_id": v["user_id"], "jwt_claim": v["jwt_claim"]},
    ),
]

_AUTH_ERROR: list[_TemplateEntry] = [
    (
        "JWT signature verification failed for user {user_id}: invalid signature",
        '{ts} ERROR auth-service: jwt_verification_failed user_id={user_id} error=InvalidSignatureError ip={client_ip}',
        lambda v: {"user_id": v["user_id"], "client_ip": v["client_ip"]},
    ),
    (
        "Authentication service connection pool exhausted: {pool_active}/{pool_max} connections",
        '{ts} ERROR auth-service: pool_exhausted active={pool_active} max={pool_max} wait_queue={wait_queue}',
        lambda v: {"pool_active": v["pool_active"], "pool_max": v["pool_max"],
                    "wait_queue": v["wait_queue"]},
    ),
]

# ---- Order templates ------------------------------------------------------

_ORDER_INFO: list[_TemplateEntry] = [
    (
        "Order {order_id} created by user {user_id} with {item_count} items totalling ${total}",
        '{ts} INFO order-service: order_created order_id={order_id} user_id={user_id} items={item_count} total=${total}',
        lambda v: {"order_id": v["order_id"], "user_id": v["user_id"],
                    "item_count": v["item_count"], "total": v["total"]},
    ),
    (
        "Order {order_id} status changed from {from_status} to {to_status}",
        '{ts} INFO order-service: status_change order_id={order_id} from={from_status} to={to_status}',
        lambda v: {"order_id": v["order_id"], "from_status": v["from_status"],
                    "to_status": v["to_status"]},
    ),
]

_ORDER_WARNING: list[_TemplateEntry] = [
    (
        "Order {order_id} processing delayed: downstream latency {duration_ms}ms exceeds SLA",
        '{ts} WARN order-service: sla_breach order_id={order_id} latency={duration_ms}ms sla_limit=500ms',
        lambda v: {"order_id": v["order_id"], "duration_ms": v["duration_ms"]},
    ),
    (
        "Inventory reservation timeout for order {order_id} item {item_sku}",
        '{ts} WARN order-service: reservation_timeout order_id={order_id} sku={item_sku} timeout=3000ms',
        lambda v: {"order_id": v["order_id"], "item_sku": v["item_sku"]},
    ),
]

_ORDER_ERROR: list[_TemplateEntry] = [
    (
        "Order {order_id} failed: payment declined for user {user_id}",
        '{ts} ERROR order-service: order_failed order_id={order_id} user_id={user_id} reason=payment_declined',
        lambda v: {"order_id": v["order_id"], "user_id": v["user_id"]},
    ),
    (
        "Idempotency conflict: duplicate order submission {order_id} from user {user_id}",
        '{ts} ERROR order-service: idempotency_conflict order_id={order_id} user_id={user_id}',
        lambda v: {"order_id": v["order_id"], "user_id": v["user_id"]},
    ),
]

# ---- Payment templates ----------------------------------------------------

_PAYMENT_INFO: list[_TemplateEntry] = [
    (
        "Payment {txn_id} of ${amount} processed for order {order_id} via {provider}",
        '{ts} INFO payment-gateway: payment_processed txn_id={txn_id} order_id={order_id} amount=${amount} provider={provider} latency={duration_ms}ms',
        lambda v: {"txn_id": v["txn_id"], "order_id": v["order_id"],
                    "amount": v["amount"], "provider": v["provider"],
                    "duration_ms": v["duration_ms"]},
    ),
    (
        "Refund {txn_id} of ${amount} initiated for order {order_id}",
        '{ts} INFO payment-gateway: refund_initiated txn_id={txn_id} order_id={order_id} amount=${amount}',
        lambda v: {"txn_id": v["txn_id"], "order_id": v["order_id"],
                    "amount": v["amount"]},
    ),
]

_PAYMENT_WARNING: list[_TemplateEntry] = [
    (
        "Payment provider {provider} latency elevated: {duration_ms}ms (threshold 500ms)",
        '{ts} WARN payment-gateway: provider_slow provider={provider} latency={duration_ms}ms threshold=500ms',
        lambda v: {"provider": v["provider"], "duration_ms": v["duration_ms"]},
    ),
    (
        "Payment retry attempt {attempt}/3 for transaction {txn_id}",
        '{ts} WARN payment-gateway: payment_retry txn_id={txn_id} attempt={attempt} max_retries=3',
        lambda v: {"txn_id": v["txn_id"], "attempt": v["attempt"]},
    ),
]

_PAYMENT_ERROR: list[_TemplateEntry] = [
    (
        "Payment {txn_id} declined: insufficient funds for order {order_id}",
        '{ts} ERROR payment-gateway: payment_declined txn_id={txn_id} order_id={order_id} reason=insufficient_funds',
        lambda v: {"txn_id": v["txn_id"], "order_id": v["order_id"]},
    ),
    (
        "Payment provider {provider} unreachable: connection refused after {duration_ms}ms",
        '{ts} ERROR payment-gateway: provider_unreachable provider={provider} error=ConnectionRefusedError latency={duration_ms}ms',
        lambda v: {"provider": v["provider"], "duration_ms": v["duration_ms"]},
    ),
]

# ---- Database templates ---------------------------------------------------

_DATABASE_INFO: list[_TemplateEntry] = [
    (
        "SELECT on {table} completed in {duration_ms}ms returning {row_count} rows",
        '{ts} INFO inventory-db: query_executed op=SELECT table={table} rows={row_count} duration={duration_ms}ms',
        lambda v: {"table": v["table"], "row_count": v["row_count"],
                    "duration_ms": v["duration_ms"]},
    ),
    (
        "INSERT into {table}: {row_count} rows affected in {duration_ms}ms",
        '{ts} INFO inventory-db: query_executed op=INSERT table={table} rows={row_count} duration={duration_ms}ms',
        lambda v: {"table": v["table"], "row_count": v["row_count"],
                    "duration_ms": v["duration_ms"]},
    ),
    (
        "Connection pool stats: active={pool_active} idle={pool_idle} max={pool_max}",
        '{ts} INFO inventory-db: pool_stats active={pool_active} idle={pool_idle} max={pool_max}',
        lambda v: {"pool_active": v["pool_active"], "pool_idle": v["pool_idle"],
                    "pool_max": v["pool_max"]},
    ),
]

_DATABASE_WARNING: list[_TemplateEntry] = [
    (
        "Slow query on {table}: {duration_ms}ms exceeds threshold of 100ms",
        '{ts} WARN inventory-db: slow_query table={table} duration={duration_ms}ms threshold=100ms query_hash={query_hash}',
        lambda v: {"table": v["table"], "duration_ms": v["duration_ms"],
                    "query_hash": v["query_hash"]},
    ),
    (
        "Connection pool utilization at {pool_pct}%: {pool_active}/{pool_max}",
        '{ts} WARN inventory-db: pool_pressure active={pool_active} max={pool_max} utilization={pool_pct}%',
        lambda v: {"pool_active": v["pool_active"], "pool_max": v["pool_max"],
                    "pool_pct": v["pool_pct"]},
    ),
]

_DATABASE_ERROR: list[_TemplateEntry] = [
    (
        "Deadlock detected on {table}: transaction rolled back after {duration_ms}ms",
        '{ts} ERROR inventory-db: deadlock table={table} duration={duration_ms}ms txn_id={txn_id}',
        lambda v: {"table": v["table"], "duration_ms": v["duration_ms"],
                    "txn_id": v["txn_id"]},
    ),
    (
        "Connection to PostgreSQL lost: retrying in {retry_delay_s}s (attempt {attempt}/5)",
        '{ts} ERROR inventory-db: connection_lost retry_delay={retry_delay_s}s attempt={attempt} max=5',
        lambda v: {"retry_delay_s": v["retry_delay_s"], "attempt": v["attempt"]},
    ),
]

# ---- Generic fallback templates -------------------------------------------

_GENERIC_INFO: list[_TemplateEntry] = [
    (
        "Service heartbeat OK: uptime {uptime_s}s",
        '{ts} INFO {service_name}: heartbeat status=OK uptime={uptime_s}s',
        lambda v: {"uptime_s": v["uptime_s"]},
    ),
    (
        "Configuration reloaded: {config_key} updated",
        '{ts} INFO {service_name}: config_reload key={config_key} source=env',
        lambda v: {"config_key": v["config_key"]},
    ),
]

_GENERIC_WARNING: list[_TemplateEntry] = [
    (
        "Memory usage at {mem_pct}%: approaching threshold",
        '{ts} WARN {service_name}: memory_pressure used_pct={mem_pct}% threshold=85%',
        lambda v: {"mem_pct": v["mem_pct"]},
    ),
]

_GENERIC_ERROR: list[_TemplateEntry] = [
    (
        "Unhandled exception in request handler: {error_class}: {error_msg}",
        '{ts} ERROR {service_name}: unhandled_exception type={error_class} message="{error_msg}"',
        lambda v: {"error_class": v["error_class"], "error_msg": v["error_msg"]},
    ),
]


# ---------------------------------------------------------------------------
# Template registry lookup
# ---------------------------------------------------------------------------

_REGISTRY: dict[tuple[str, str], list[_TemplateEntry]] = {
    ("gateway", "info"): _GATEWAY_INFO,
    ("gateway", "warning"): _GATEWAY_WARNING,
    ("gateway", "error"): _GATEWAY_ERROR,
    ("auth", "info"): _AUTH_INFO,
    ("auth", "warning"): _AUTH_WARNING,
    ("auth", "error"): _AUTH_ERROR,
    ("order", "info"): _ORDER_INFO,
    ("order", "warning"): _ORDER_WARNING,
    ("order", "error"): _ORDER_ERROR,
    ("payment", "info"): _PAYMENT_INFO,
    ("payment", "warning"): _PAYMENT_WARNING,
    ("payment", "error"): _PAYMENT_ERROR,
    ("database", "info"): _DATABASE_INFO,
    ("database", "warning"): _DATABASE_WARNING,
    ("database", "error"): _DATABASE_ERROR,
}


def _build_template_vars(rng: random.Random) -> dict[str, Any]:
    """Build a shared variable dictionary for template interpolation."""
    now = datetime.now(timezone.utc)
    return {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "client_ip": _random_ipv4(rng),
        "user_id": _random_user_id(rng),
        "order_id": _random_order_id(rng),
        "txn_id": _random_txn_id(rng),
        "method": _random_http_method(rng),
        "path": _random_path(rng),
        "status": 200,  # overwritten per-level below
        "duration_ms": round(rng.uniform(1.0, 500.0), 2),
        "bytes": rng.randint(128, 65536),
        "remaining": rng.randint(0, 1000),
        "upstream": rng.choice(["auth-service", "order-service",
                                "payment-gateway", "inventory-db"]),
        "failures": rng.randint(3, 20),
        "expiry_s": rng.choice([900, 1800, 3600, 7200]),
        "auth_method": rng.choice(["password", "oauth2", "api_key", "sso"]),
        "attempts": rng.randint(1, 5),
        "jwt_claim": _random_jwt_claim(rng),
        "pool_active": rng.randint(5, 20),
        "pool_idle": rng.randint(0, 10),
        "pool_max": 20,
        "wait_queue": rng.randint(0, 15),
        "item_count": rng.randint(1, 12),
        "total": round(rng.uniform(9.99, 999.99), 2),
        "from_status": rng.choice(["pending", "confirmed", "processing"]),
        "to_status": rng.choice(["confirmed", "processing", "shipped", "delivered"]),
        "item_sku": f"SKU-{rng.randint(10000, 99999)}",
        "amount": round(rng.uniform(5.0, 2500.0), 2),
        "provider": rng.choice(["stripe", "paypal", "adyen", "square"]),
        "attempt": rng.randint(1, 3),
        "table": _random_sql_table(rng),
        "row_count": rng.randint(1, 5000),
        "query_hash": uuid.uuid4().hex[:12],
        "pool_pct": rng.randint(70, 98),
        "retry_delay_s": rng.choice([1, 2, 5, 10]),
        "uptime_s": rng.randint(60, 864000),
        "config_key": rng.choice(["log_level", "pool_size", "cache_ttl",
                                   "rate_limit", "feature_flags"]),
        "mem_pct": rng.randint(75, 97),
        "error_class": rng.choice(["ValueError", "RuntimeError", "KeyError",
                                    "TimeoutError", "ConnectionError"]),
        "error_msg": rng.choice([
            "unexpected None in required field",
            "operation timed out after 30s",
            "failed to serialize response payload",
            "maximum retry attempts exceeded",
        ]),
        "service_name": "service",  # placeholder — overwritten by caller
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_log(
    service_type: str,
    service_name: str,
    level: str,
    rng: random.Random,
    base_latency_ms: float = 50.0,
    latency_jitter_ms: float = 25.0,
) -> tuple[str, str, dict[str, Any]]:
    """Generate a realistic ``(message, raw, metadata)`` triple.

    Parameters
    ----------
    service_type:
        Semantic tag (``gateway``, ``auth``, ``order``, ``payment``,
        ``database``, or anything else for the generic fallback).
    service_name:
        Actual service name written into raw-log lines.
    level:
        One of ``info``, ``warning``, ``error``.
    rng:
        A ``random.Random`` instance for deterministic generation.
    base_latency_ms / latency_jitter_ms:
        Used to compute a realistic ``duration_ms`` value.

    Returns
    -------
    tuple[str, str, dict[str, Any]]
        ``(message, raw_log_line, metadata_dict)``
    """
    templates = _REGISTRY.get((service_type, level))
    if templates is None:
        # Fall back to generic templates
        fallback: dict[str, list[_TemplateEntry]] = {
            "info": _GENERIC_INFO,
            "warning": _GENERIC_WARNING,
            "error": _GENERIC_ERROR,
        }
        templates = fallback.get(level, _GENERIC_INFO)

    msg_fmt, raw_fmt, meta_fn = rng.choice(templates)

    variables = _build_template_vars(rng)
    variables["service_name"] = service_name
    variables["duration_ms"] = _random_duration_ms(base_latency_ms, latency_jitter_ms, rng)
    variables["status"] = _random_status_for_level(level, rng)

    message = msg_fmt.format_map(variables)
    raw = raw_fmt.format_map(variables)
    metadata = meta_fn(variables)

    return message, raw, metadata

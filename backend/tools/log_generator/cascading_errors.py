"""Multi-service cascading exception engine.

Simulates realistic failure propagation across the ``ServiceTopology``
DAG.  When a root-cause failure is injected at a leaf or internal
service, the engine walks **upstream** through the graph (toward callers)
generating progressively degraded symptom logs — connection timeouts,
circuit breaker trips, HTTP 502/504 responses — that share a single
``correlation_id`` so the backend anomaly detector can correlate them.
"""

from __future__ import annotations

import random
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .topology import ServiceTopology

# Re-use the wire-compatible LogEntry from the generator module.
# Import is deferred to the function body to avoid circular imports
# at module load time if someone imports cascading_errors before generator.


# ---------------------------------------------------------------------------
# Realistic multi-line stack traces and exception strings
# ---------------------------------------------------------------------------

_DB_STACK_TRACES: list[str] = [
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/repositories/inventory_repo.py", line 142, in execute_query
            result = await connection.execute(stmt)
          File "/usr/local/lib/python3.12/site-packages/sqlalchemy/engine/default.py", line 921, in execute
            return self._execute_clauseelement(elem)
          File "/usr/local/lib/python3.12/site-packages/asyncpg/connection.py", line 341, in execute
            return await self._protocol.query(query, timeout)
        asyncpg.exceptions.ConnectionDoesNotExistError: connection was closed in the middle of operation"""),
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/repositories/inventory_repo.py", line 87, in get_stock_level
            async with self.engine.begin() as conn:
          File "/usr/local/lib/python3.12/site-packages/sqlalchemy/ext/asyncio/engine.py", line 194, in __aenter__
            return await self._connection.__aenter__()
        sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 10 reached, connection timed out, timeout 30.00"""),
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/repositories/inventory_repo.py", line 211, in update_stock
            await session.commit()
          File "/usr/local/lib/python3.12/site-packages/sqlalchemy/ext/asyncio/session.py", line 370, in commit
            await self._proxied.commit()
        sqlalchemy.exc.OperationalError: (asyncpg.DeadlockDetectedError) deadlock detected
        DETAIL:  Process 4827 waits for ShareLock on transaction 98712; blocked by process 4831.
        Process 4831 waits for ShareLock on transaction 98710; blocked by process 4827."""),
]

_AUTH_STACK_TRACES: list[str] = [
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/security/jwt_handler.py", line 63, in verify_token
            payload = jwt.decode(token, key, algorithms=["RS256"])
          File "/usr/local/lib/python3.12/site-packages/jwt/api_jwt.py", line 168, in decode
            self._validate_claims(payload, merged_options)
        jwt.exceptions.ExpiredSignatureError: Signature has expired"""),
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/security/jwt_handler.py", line 48, in load_signing_key
            key_data = await self._fetch_jwks(jwks_uri)
          File "/app/security/jwt_handler.py", line 55, in _fetch_jwks
            resp = await self._http.get(jwks_uri, timeout=5.0)
        httpx.ConnectError: [Errno 111] Connection refused"""),
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/security/jwt_handler.py", line 91, in rotate_keys
            new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
          File "/app/security/key_store.py", line 37, in persist_key
            await self._redis.set(f"signing_key:{kid}", pem_bytes)
        redis.exceptions.ConnectionError: Error 10061 connecting to redis:6379. No connection could be made."""),
]

_NETWORK_STACK_TRACES: list[str] = [
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/clients/service_client.py", line 78, in call_downstream
            resp = await self._http.post(url, json=payload, timeout=10.0)
          File "/usr/local/lib/python3.12/site-packages/httpx/_client.py", line 1842, in post
            return await self.request("POST", url, json=json, timeout=timeout)
        httpx.ReadTimeout: timed out"""),
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/clients/service_client.py", line 78, in call_downstream
            resp = await self._http.post(url, json=payload, timeout=10.0)
          File "/usr/local/lib/python3.12/site-packages/httpx/_transports/default.py", line 69, in handle_async_request
            resp = await self._pool.handle_async_request(req)
        httpx.RemoteProtocolError: peer closed connection without sending complete message body"""),
]

_PAYMENT_STACK_TRACES: list[str] = [
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/gateways/stripe_gateway.py", line 112, in charge
            intent = await stripe.PaymentIntent.create_async(amount=amount_cents, currency="usd")
          File "/usr/local/lib/python3.12/site-packages/stripe/_api_requestor.py", line 529, in request_async
            raise self.specific_api_error(rbody, rcode, resp)
        stripe.error.CardError: Your card was declined. (charge_declined)"""),
    textwrap.dedent("""\
        Traceback (most recent call last):
          File "/app/gateways/stripe_gateway.py", line 95, in charge
            resp = await self._http.post(STRIPE_API, headers=headers, json=body, timeout=15.0)
        httpx.ConnectTimeout: timed out"""),
]


# ---------------------------------------------------------------------------
# Propagated symptom templates
# ---------------------------------------------------------------------------
# Each entry: (message_fmt, raw_fmt)
# Shared variables: {failed_service}, {correlation_id}, {duration_ms},
#                   {service_name}, {ts}, {status}, {retry_count}

_SYMPTOM_TEMPLATES: list[tuple[str, str]] = [
    (
        "Downstream service {failed_service} returned HTTP {status} after {duration_ms}ms",
        '{ts} ERROR {service_name}: downstream_error target={failed_service} status={status} latency={duration_ms}ms correlation_id={correlation_id}',
    ),
    (
        "Circuit breaker OPEN for {failed_service}: {failure_count} failures in last 60s",
        '{ts} ERROR {service_name}: circuit_breaker_open target={failed_service} failures={failure_count} window=60s correlation_id={correlation_id}',
    ),
    (
        "Connection timeout to {failed_service} after {duration_ms}ms — request dropped",
        '{ts} ERROR {service_name}: connection_timeout target={failed_service} timeout={duration_ms}ms correlation_id={correlation_id}',
    ),
    (
        "Retry {retry_count}/3 to {failed_service} failed: upstream unavailable",
        '{ts} WARN {service_name}: retry_exhausted target={failed_service} attempt={retry_count} max=3 correlation_id={correlation_id}',
    ),
    (
        "Fallback activated for {failed_service}: serving stale cache entry",
        '{ts} WARN {service_name}: fallback_activated target={failed_service} strategy=stale_cache correlation_id={correlation_id}',
    ),
    (
        "Request aborted: dependency {failed_service} unreachable — returning {status} to caller",
        '{ts} ERROR {service_name}: request_aborted dependency={failed_service} status={status} correlation_id={correlation_id}',
    ),
]


# ---------------------------------------------------------------------------
# Root-cause error type definitions
# ---------------------------------------------------------------------------

_ERROR_TYPE_REGISTRY: dict[str, dict[str, Any]] = {
    "connection_pool_exhaustion": {
        "service_types": ["database"],
        "stack_traces": _DB_STACK_TRACES,
        "message": "Connection pool exhausted: QueuePool limit reached — all {pool_max} connections in use, {overflow} overflow active",
        "raw": '{ts} CRITICAL {service_name}: pool_exhausted pool_size={pool_max} overflow={overflow} queued={queued} correlation_id={correlation_id}',
    },
    "deadlock": {
        "service_types": ["database"],
        "stack_traces": _DB_STACK_TRACES,
        "message": "Deadlock detected on table {table}: transaction rolled back after {duration_ms}ms",
        "raw": '{ts} CRITICAL {service_name}: deadlock table={table} duration={duration_ms}ms txn_id={txn_id} correlation_id={correlation_id}',
    },
    "auth_key_rotation_failure": {
        "service_types": ["auth"],
        "stack_traces": _AUTH_STACK_TRACES,
        "message": "Signing key rotation failed: unable to persist new RSA key to key store",
        "raw": '{ts} CRITICAL {service_name}: key_rotation_failed error=ConnectionError target=redis:6379 correlation_id={correlation_id}',
    },
    "jwt_verification_failure": {
        "service_types": ["auth"],
        "stack_traces": _AUTH_STACK_TRACES,
        "message": "JWT verification failed for all incoming requests: signing key unavailable",
        "raw": '{ts} CRITICAL {service_name}: jwt_verification_mass_failure active_sessions_affected={affected_sessions} correlation_id={correlation_id}',
    },
    "network_partition": {
        "service_types": ["gateway", "order", "payment", "database", "auth", "generic"],
        "stack_traces": _NETWORK_STACK_TRACES,
        "message": "Network partition detected: {packet_loss_pct}% packet loss to {target_host}",
        "raw": '{ts} CRITICAL {service_name}: network_partition target={target_host} packet_loss={packet_loss_pct}% rtt={rtt_ms}ms correlation_id={correlation_id}',
    },
    "payment_provider_outage": {
        "service_types": ["payment"],
        "stack_traces": _PAYMENT_STACK_TRACES,
        "message": "Payment provider {provider} unreachable: connection refused on all endpoints",
        "raw": '{ts} CRITICAL {service_name}: provider_outage provider={provider} endpoints_tried=3 last_error=ConnectTimeout correlation_id={correlation_id}',
    },
    "oom_crash": {
        "service_types": ["gateway", "order", "payment", "database", "auth", "generic"],
        "stack_traces": [
            textwrap.dedent("""\
                Fatal error: Out of memory (allocated 2147483648 bytes)
                  Current RSS: 2048 MB / Limit: 2048 MB
                  Heap dump written to /tmp/heapdump-20260723-193045.hprof
                  Process will be terminated by OOM killer"""),
        ],
        "message": "Process terminated by OOM killer: RSS {rss_mb}MB exceeded limit {limit_mb}MB",
        "raw": '{ts} CRITICAL {service_name}: oom_kill rss_mb={rss_mb} limit_mb={limit_mb} correlation_id={correlation_id}',
    },
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class CascadingExceptionEngine:
    """Simulates multi-service cascading failures across a ``ServiceTopology``.

    Parameters
    ----------
    topology:
        The service topology DAG.
    seed:
        Optional RNG seed for reproducible generation.
    """

    def __init__(
        self,
        topology: ServiceTopology,
        seed: int | None = None,
    ) -> None:
        self._topology = topology
        self._rng = random.Random(seed)

    def trigger_cascade(
        self,
        root_service: str,
        error_type: str,
        correlation_id: str | None = None,
    ) -> list[Any]:
        """Inject a root-cause failure and propagate symptoms upstream.

        Parameters
        ----------
        root_service:
            The service where the failure originates (e.g. ``"inventory-db"``).
        error_type:
            One of the keys in ``_ERROR_TYPE_REGISTRY`` (e.g.
            ``"connection_pool_exhaustion"``, ``"deadlock"``,
            ``"auth_key_rotation_failure"``, ``"network_partition"``).
        correlation_id:
            Shared trace identifier.  Auto-generated if ``None``.

        Returns
        -------
        list[LogEntry]
            The root-cause log followed by propagated symptom logs
            in reverse-topological order (deepest service first).
        """
        from .generator import LogEntry

        if error_type not in _ERROR_TYPE_REGISTRY:
            raise ValueError(
                f"Unknown error_type '{error_type}'. "
                f"Available: {sorted(_ERROR_TYPE_REGISTRY)}"
            )

        correlation_id = correlation_id or str(uuid.uuid4())
        error_def = _ERROR_TYPE_REGISTRY[error_type]
        base_ts = datetime.now(timezone.utc)

        # --- Root-cause log ---
        root_vars = self._build_root_vars(root_service, correlation_id, base_ts)
        stack_trace = self._rng.choice(error_def["stack_traces"])
        message = error_def["message"].format_map(root_vars)
        raw_line = error_def["raw"].format_map(root_vars)
        # Append multi-line stack trace to the raw field for Drain3 testing.
        raw_with_trace = f"{raw_line}\n{stack_trace}"

        root_metadata: dict[str, Any] = {
            "correlation_id": correlation_id,
            "root_cause": True,
            "propagated_symptom": False,
            "error_type": error_type,
            "stack_trace": stack_trace,
        }

        root_log = LogEntry(
            timestamp=base_ts,
            service_name=root_service,
            level="error",
            message=message,
            metadata=root_metadata,
            raw=raw_with_trace,
        )

        logs: list[LogEntry] = [root_log]

        # --- Propagate symptoms upstream ---
        visited: set[str] = {root_service}
        frontier: list[tuple[str, str]] = [
            (service_name, root_service)
            for service_name in self._topology.upstream_of(root_service)
        ]
        hop_index = 1

        while frontier:
            next_frontier: list[tuple[str, str]] = []
            for service_name, failed_dependency in frontier:
                if service_name in visited:
                    continue
                visited.add(service_name)

                symptom_ts = base_ts + timedelta(
                    milliseconds=hop_index * self._rng.uniform(50, 300),
                )
                symptom_log = self._generate_symptom(
                    service_name=service_name,
                    failed_service=failed_dependency,
                    error_type=error_type,
                    correlation_id=correlation_id,
                    timestamp=symptom_ts,
                    hop_index=hop_index,
                )
                logs.append(symptom_log)

                # Continue propagating further upstream.
                for upstream in self._topology.upstream_of(service_name):
                    if upstream not in visited:
                        next_frontier.append((upstream, service_name))

            frontier = next_frontier
            hop_index += 1

        return logs

    @property
    def available_error_types(self) -> list[str]:
        """Return all registered error type keys."""
        return sorted(_ERROR_TYPE_REGISTRY)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_root_vars(
        self,
        service_name: str,
        correlation_id: str,
        ts: datetime,
    ) -> dict[str, Any]:
        """Build the variable dictionary for root-cause template rendering."""
        return {
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "service_name": service_name,
            "correlation_id": correlation_id,
            "pool_max": 20,
            "overflow": self._rng.randint(8, 10),
            "queued": self._rng.randint(15, 50),
            "table": self._rng.choice(["orders", "inventory", "payments", "sessions"]),
            "duration_ms": round(self._rng.uniform(200, 5000), 2),
            "txn_id": f"txn_{uuid.uuid4().hex[:16]}",
            "affected_sessions": self._rng.randint(500, 5000),
            "packet_loss_pct": self._rng.randint(30, 95),
            "target_host": f"{service_name}.internal.svc:8080",
            "rtt_ms": round(self._rng.uniform(500, 10000), 2),
            "provider": self._rng.choice(["stripe", "paypal", "adyen", "square"]),
            "rss_mb": self._rng.choice([2048, 4096, 8192]),
            "limit_mb": self._rng.choice([2048, 4096, 8192]),
        }

    def _generate_symptom(
        self,
        service_name: str,
        failed_service: str,
        error_type: str,
        correlation_id: str,
        timestamp: datetime,
        hop_index: int,
    ) -> Any:
        """Generate a single propagated symptom ``LogEntry``."""
        from .generator import LogEntry

        template = self._rng.choice(_SYMPTOM_TEMPLATES)
        msg_fmt, raw_fmt = template

        # Higher hops get higher latencies and worse status codes.
        duration_ms = round(self._rng.uniform(500, 5000) * (1 + hop_index * 0.3), 2)
        status = self._rng.choice([502, 503, 504])
        failure_count = self._rng.randint(5 + hop_index * 3, 20 + hop_index * 5)
        retry_count = self._rng.randint(1, 3)

        variables = {
            "ts": timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "service_name": service_name,
            "failed_service": failed_service,
            "correlation_id": correlation_id,
            "duration_ms": duration_ms,
            "status": status,
            "failure_count": failure_count,
            "retry_count": retry_count,
        }

        # Determine level: warnings for retries/fallbacks, errors for the rest.
        level = "warning" if "retry" in msg_fmt.lower() or "fallback" in msg_fmt.lower() else "error"

        message = msg_fmt.format_map(variables)
        raw = raw_fmt.format_map(variables)

        metadata: dict[str, Any] = {
            "correlation_id": correlation_id,
            "root_cause": False,
            "propagated_symptom": True,
            "error_type": error_type,
            "failed_service": failed_service,
            "hop_index": hop_index,
            "status": status,
            "duration_ms": duration_ms,
        }

        return LogEntry(
            timestamp=timestamp,
            service_name=service_name,
            level=level,
            message=message,
            metadata=metadata,
            raw=raw,
        )

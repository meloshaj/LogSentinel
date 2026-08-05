"""Pre-packaged stress-test scenarios for the LogSentinel backend.

Each scenario is a self-contained timeline of log batches that models a
specific architectural failure mode.  Scenarios expose a **step-by-step
iterator** so the caller can advance through the timeline at its own pace
(useful for both real-time streaming and bulk generation).

Implemented scenarios
---------------------
* ``DatabasePoolExhaustionScenario`` — gradual latency spike in
  ``inventory-db`` → pool exhaustion → deadlocks → cascading failures
  in ``order-service`` and ``api-gateway``.
* ``AuthTokenStormScenario`` — ``auth-service`` key-rotation failure →
  mass JWT verification failures → 401 flood at ``api-gateway``.
* ``NetworkPartitionScenario`` — intermittent packet loss between two
  dependent services → retries → eventual total failure.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from .cascading_errors import CascadingExceptionEngine
from .config import GeneratorConfig, default_ecommerce_topology
from .topology import ServiceTopology


# ---------------------------------------------------------------------------
# Scenario step model
# ---------------------------------------------------------------------------


class ScenarioStep:
    """One discrete step in a scenario timeline.

    Attributes
    ----------
    step_index:
        Zero-based position in the timeline.
    phase:
        Human-readable phase label (e.g. ``"baseline"``, ``"degradation"``,
        ``"failure"``, ``"cascade"``, ``"recovery"``).
    description:
        Short sentence describing what happens in this step.
    logs:
        The ``LogEntry`` objects to emit in this step.
    correlation_id:
        The trace identifier shared across cascade events (if any).
    """

    __slots__ = ("step_index", "phase", "description", "logs", "correlation_id")

    def __init__(
        self,
        step_index: int,
        phase: str,
        description: str,
        logs: list[Any],
        correlation_id: str | None = None,
    ) -> None:
        self.step_index = step_index
        self.phase = phase
        self.description = description
        self.logs = logs
        self.correlation_id = correlation_id

    def __repr__(self) -> str:
        return (
            f"ScenarioStep(step={self.step_index}, phase={self.phase!r}, "
            f"logs={len(self.logs)}, cid={self.correlation_id and self.correlation_id[:8]})"
        )


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class BaseScenario:
    """Abstract base for all scenario implementations."""

    name: str = "base"
    description: str = ""

    def __init__(
        self,
        config: GeneratorConfig | None = None,
        seed: int | None = None,
    ) -> None:
        self._config = config or default_ecommerce_topology()
        self._rng = random.Random(seed)
        self._topology = ServiceTopology(self._config.services)
        self._cascade_engine = CascadingExceptionEngine(self._topology, seed=seed)
        self._steps: list[ScenarioStep] = []
        self._build_timeline()

    def _build_timeline(self) -> None:
        """Subclasses must populate ``self._steps``."""
        raise NotImplementedError

    def __len__(self) -> int:
        return len(self._steps)

    def __iter__(self) -> Iterator[ScenarioStep]:
        return iter(self._steps)

    def get_step(self, index: int) -> ScenarioStep:
        """Return a specific step by index."""
        return self._steps[index]

    def summary(self) -> dict[str, Any]:
        """Return a JSON-friendly scenario summary."""
        return {
            "name": self.name,
            "description": self.description,
            "total_steps": len(self._steps),
            "phases": [s.phase for s in self._steps],
            "total_logs": sum(len(s.logs) for s in self._steps),
        }

    # ------------------------------------------------------------------
    # Helpers for subclasses
    # ------------------------------------------------------------------

    def _normal_background_logs(self, count: int) -> list[Any]:
        """Generate ``count`` normal info-level background noise logs."""
        from .generator import LogEntry
        from .templates import render_log

        logs: list[Any] = []
        for _ in range(count):
            svc = self._rng.choice(self._topology.service_names)
            node = self._topology.get_node(svc)
            message, raw, metadata = render_log(
                service_type=node.service_type,
                service_name=node.service_name,
                level="info",
                rng=self._rng,
                base_latency_ms=node.base_latency_ms,
                latency_jitter_ms=node.latency_jitter_ms,
            )
            logs.append(LogEntry(
                timestamp=datetime.now(timezone.utc),
                service_name=svc,
                level="info",
                message=message,
                metadata=metadata,
                raw=raw,
            ))
        return logs

    def _degradation_logs(
        self,
        service_name: str,
        count: int,
        latency_multiplier: float = 3.0,
    ) -> list[Any]:
        """Generate ``count`` warning-level degradation logs for a service."""
        from .generator import LogEntry
        from .templates import render_log

        node = self._topology.get_node(service_name)
        logs: list[Any] = []
        for _ in range(count):
            message, raw, metadata = render_log(
                service_type=node.service_type,
                service_name=node.service_name,
                level="warning",
                rng=self._rng,
                base_latency_ms=node.base_latency_ms * latency_multiplier,
                latency_jitter_ms=node.latency_jitter_ms * latency_multiplier,
            )
            metadata["degradation"] = True
            metadata["latency_multiplier"] = latency_multiplier
            logs.append(LogEntry(
                timestamp=datetime.now(timezone.utc),
                service_name=service_name,
                level="warning",
                message=message,
                metadata=metadata,
                raw=raw,
            ))
        return logs


# ---------------------------------------------------------------------------
# Scenario 1: Database Pool Exhaustion
# ---------------------------------------------------------------------------


class DatabasePoolExhaustionScenario(BaseScenario):
    """Simulates a gradual latency spike in ``inventory-db`` culminating
    in connection-pool deadlocks and cascading transaction failures.

    Timeline
    --------
    0. **baseline** — normal traffic across all services.
    1. **degradation** — ``inventory-db`` latency increases 3×, slow query warnings.
    2. **escalation** — ``inventory-db`` latency 8×, pool pressure warnings.
    3. **failure** — pool exhaustion + deadlock at ``inventory-db``.
    4. **cascade** — failures propagate to ``order-service`` then ``api-gateway``.
    5. **recovery** — normal traffic resumes (background noise).
    """

    name = "database_pool_exhaustion"
    description = (
        "Gradual inventory-db latency spike -> pool exhaustion -> "
        "deadlocks -> cascading failures in order-service and api-gateway"
    )

    def _build_timeline(self) -> None:
        cid = str(uuid.uuid4())

        # Step 0: baseline
        self._steps.append(ScenarioStep(
            step_index=0,
            phase="baseline",
            description="Normal traffic across all services",
            logs=self._normal_background_logs(20),
        ))

        # Step 1: degradation (3× latency)
        self._steps.append(ScenarioStep(
            step_index=1,
            phase="degradation",
            description="inventory-db latency increasing, slow query warnings",
            logs=(
                self._normal_background_logs(10)
                + self._degradation_logs("inventory-db", 8, latency_multiplier=3.0)
            ),
        ))

        # Step 2: escalation (8× latency, pool pressure)
        self._steps.append(ScenarioStep(
            step_index=2,
            phase="escalation",
            description="inventory-db latency critical, pool utilization >90%",
            logs=(
                self._normal_background_logs(5)
                + self._degradation_logs("inventory-db", 12, latency_multiplier=8.0)
            ),
        ))

        # Step 3: failure — pool exhaustion + deadlock
        pool_cascade = self._cascade_engine.trigger_cascade(
            root_service="inventory-db",
            error_type="connection_pool_exhaustion",
            correlation_id=cid,
        )
        deadlock_cascade = self._cascade_engine.trigger_cascade(
            root_service="inventory-db",
            error_type="deadlock",
            correlation_id=cid,
        )
        self._steps.append(ScenarioStep(
            step_index=3,
            phase="failure",
            description="Connection pool exhaustion and deadlock at inventory-db",
            logs=(
                self._normal_background_logs(3)
                + pool_cascade
                + deadlock_cascade
            ),
            correlation_id=cid,
        ))

        # Step 4: cascade — sustained errors across order-service and api-gateway + BURST
        sustained_cascade = self._cascade_engine.trigger_cascade(
            root_service="inventory-db",
            error_type="connection_pool_exhaustion",
            correlation_id=cid,
        )
        
        # Inject 500 explicit CRITICAL/ERROR log bursts tagged with correlation_id
        from .generator import LogEntry
        from datetime import timedelta
        burst_logs: list[Any] = []
        base_ts = datetime.now(timezone.utc)
        
        for i in range(500):
            ts = base_ts + timedelta(milliseconds=i * 10)
            svc_index = i % 3
            
            if svc_index == 0:
                # inventory-db (root cause)
                message = "sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 10 reached, connection timed out, timeout 30.00"
                raw = (
                    f'{ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]}Z CRITICAL inventory-db: '
                    f'{message} correlation_id={cid}'
                )
                burst_logs.append(LogEntry(
                    timestamp=ts,
                    service_name="inventory-db",
                    level="critical",
                    message=message,
                    metadata={
                        "correlation_id": cid,
                        "root_cause": True,
                        "error_type": "connection_pool_exhaustion",
                    },
                    raw=raw,
                ))
            elif svc_index == 1:
                # order-service (symptom)
                message = "CRITICAL BURST: Database connection pool completely exhausted and transaction failed"
                raw = (
                    f'{ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]}Z CRITICAL order-service: '
                    f'{message} target=inventory-db correlation_id={cid}'
                )
                burst_logs.append(LogEntry(
                    timestamp=ts,
                    service_name="order-service",
                    level="critical",
                    message=message,
                    metadata={
                        "correlation_id": cid,
                        "root_cause": False,
                        "propagated_symptom": True,
                        "error_type": "connection_pool_exhaustion",
                        "target_service": "inventory-db",
                        "parent_service": "inventory-db",
                        "status": 503,
                    },
                    raw=raw,
                ))
            else:
                # api-gateway (symptom)
                message = "CRITICAL BURST: Database connection pool completely exhausted and transaction failed"
                raw = (
                    f'{ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]}Z CRITICAL api-gateway: '
                    f'{message} target=inventory-db correlation_id={cid}'
                )
                burst_logs.append(LogEntry(
                    timestamp=ts,
                    service_name="api-gateway",
                    level="critical",
                    message=message,
                    metadata={
                        "correlation_id": cid,
                        "root_cause": False,
                        "propagated_symptom": True,
                        "error_type": "connection_pool_exhaustion",
                        "target_service": "inventory-db",
                        "parent_service": "order-service",
                        "status": 503,
                    },
                    raw=raw,
                ))
            
        self._steps.append(ScenarioStep(
            step_index=4,
            phase="cascade",
            description="Sustained cascading failures in order-service and api-gateway with ML burst",
            logs=(
                self._normal_background_logs(5)
                + sustained_cascade
                + burst_logs
            ),
            correlation_id=cid,
        ))

        # Step 5: recovery
        self._steps.append(ScenarioStep(
            step_index=5,
            phase="recovery",
            description="Traffic returns to normal",
            logs=self._normal_background_logs(20),
        ))


# ---------------------------------------------------------------------------
# Scenario 2: Auth Token Storm
# ---------------------------------------------------------------------------


class AuthTokenStormScenario(BaseScenario):
    """Simulates an ``auth-service`` outage due to key-rotation failure,
    causing a flood of 401 Unauthorized and 503 Service Unavailable errors.

    Timeline
    --------
    0. **baseline** — normal traffic.
    1. **degradation** — auth-service latency rises, intermittent JWT warnings.
    2. **failure** — key rotation fails; JWT verification breaks for all requests.
    3. **cascade** — api-gateway receives mass 401/503 from auth-service.
    4. **storm** — elevated error rate sustained across all services.
    5. **recovery** — normal traffic resumes.
    """

    name = "auth_token_storm"
    description = (
        "Auth-service key-rotation failure -> mass JWT failures -> "
        "401/503 flood at api-gateway"
    )

    def _build_timeline(self) -> None:
        cid = str(uuid.uuid4())

        # Step 0: baseline
        self._steps.append(ScenarioStep(
            step_index=0,
            phase="baseline",
            description="Normal traffic across all services",
            logs=self._normal_background_logs(20),
        ))

        # Step 1: degradation — auth latency increase
        self._steps.append(ScenarioStep(
            step_index=1,
            phase="degradation",
            description="auth-service latency rising, intermittent JWT warnings",
            logs=(
                self._normal_background_logs(10)
                + self._degradation_logs("auth-service", 10, latency_multiplier=4.0)
            ),
        ))

        # Step 2: failure — key rotation fails
        key_cascade = self._cascade_engine.trigger_cascade(
            root_service="auth-service",
            error_type="auth_key_rotation_failure",
            correlation_id=cid,
        )
        self._steps.append(ScenarioStep(
            step_index=2,
            phase="failure",
            description="Signing key rotation fails at auth-service",
            logs=(
                self._normal_background_logs(3)
                + key_cascade
            ),
            correlation_id=cid,
        ))

        # Step 3: cascade — mass JWT verification failures
        jwt_cascade = self._cascade_engine.trigger_cascade(
            root_service="auth-service",
            error_type="jwt_verification_failure",
            correlation_id=cid,
        )
        self._steps.append(ScenarioStep(
            step_index=3,
            phase="cascade",
            description="Mass JWT verification failures, 401 flood at api-gateway",
            logs=(
                self._normal_background_logs(5)
                + jwt_cascade
                + self._generate_auth_storm_burst(cid, count=15)
            ),
            correlation_id=cid,
        ))

        # Step 4: storm — sustained elevated error rate
        self._steps.append(ScenarioStep(
            step_index=4,
            phase="storm",
            description="Sustained 401/503 error storm across gateway",
            logs=(
                self._normal_background_logs(5)
                + self._generate_auth_storm_burst(cid, count=20)
            ),
            correlation_id=cid,
        ))

        # Step 5: recovery
        self._steps.append(ScenarioStep(
            step_index=5,
            phase="recovery",
            description="Key rotation succeeds, traffic normalizes",
            logs=self._normal_background_logs(20),
        ))

    def _generate_auth_storm_burst(
        self, correlation_id: str, count: int,
    ) -> list[Any]:
        """Generate a burst of 401/503 error logs at the api-gateway."""
        from .generator import LogEntry

        logs: list[Any] = []
        ts = datetime.now(timezone.utc)
        for _ in range(count):
            status = self._rng.choice([401, 401, 401, 503])
            path = self._rng.choice([
                "/api/v1/orders", "/api/v1/users", "/api/v1/cart",
                "/api/v1/checkout", "/api/v1/products",
            ])
            user_id = f"usr_{self._rng.randint(100000, 999999)}"
            message = f"Authentication failed for {user_id}: auth-service returned {status} on {path}"
            raw = (
                f'{ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]}Z ERROR api-gateway: '
                f'auth_failed user_id={user_id} status={status} path={path} '
                f'correlation_id={correlation_id}'
            )
            logs.append(LogEntry(
                timestamp=ts,
                service_name="api-gateway",
                level="error",
                message=message,
                metadata={
                    "correlation_id": correlation_id,
                    "root_cause": False,
                    "propagated_symptom": True,
                    "error_type": "auth_token_storm",
                    "status": status,
                    "user_id": user_id,
                    "path": path,
                },
                raw=raw,
            ))
        return logs


# ---------------------------------------------------------------------------
# Scenario 3: Network Partition
# ---------------------------------------------------------------------------


class NetworkPartitionScenario(BaseScenario):
    """Simulates intermittent network failures between two dependent
    services, progressing from retries to total failure.

    Timeline
    --------
    0. **baseline** — normal traffic.
    1. **intermittent** — occasional timeouts between order-service and payment-gateway.
    2. **degradation** — retries escalate, latency spikes.
    3. **partition** — full network partition, all requests fail.
    4. **cascade** — failures propagate upstream to api-gateway.
    5. **recovery** — connectivity restored.
    """

    name = "network_partition"
    description = (
        "Intermittent packet loss between order-service and payment-gateway -> "
        "retries -> full partition -> cascading failures"
    )

    def _build_timeline(self) -> None:
        cid = str(uuid.uuid4())

        # Step 0: baseline
        self._steps.append(ScenarioStep(
            step_index=0,
            phase="baseline",
            description="Normal traffic across all services",
            logs=self._normal_background_logs(20),
        ))

        # Step 1: intermittent — occasional timeouts
        self._steps.append(ScenarioStep(
            step_index=1,
            phase="intermittent",
            description="Occasional request timeouts between order-service and payment-gateway",
            logs=(
                self._normal_background_logs(12)
                + self._generate_intermittent_failures(
                    source="order-service",
                    target="payment-gateway",
                    correlation_id=cid,
                    count=5,
                    failure_probability=0.4,
                )
            ),
        ))

        # Step 2: degradation — retries escalate
        self._steps.append(ScenarioStep(
            step_index=2,
            phase="degradation",
            description="Retry storms between order-service and payment-gateway",
            logs=(
                self._normal_background_logs(5)
                + self._generate_intermittent_failures(
                    source="order-service",
                    target="payment-gateway",
                    correlation_id=cid,
                    count=12,
                    failure_probability=0.7,
                )
            ),
        ))

        # Step 3: partition — complete failure
        partition_cascade = self._cascade_engine.trigger_cascade(
            root_service="payment-gateway",
            error_type="network_partition",
            correlation_id=cid,
        )
        self._steps.append(ScenarioStep(
            step_index=3,
            phase="partition",
            description="Full network partition: payment-gateway unreachable",
            logs=(
                self._normal_background_logs(3)
                + partition_cascade
            ),
            correlation_id=cid,
        ))

        # Step 4: cascade — upstream propagation
        sustained = self._cascade_engine.trigger_cascade(
            root_service="payment-gateway",
            error_type="network_partition",
            correlation_id=cid,
        )
        self._steps.append(ScenarioStep(
            step_index=4,
            phase="cascade",
            description="Cascading failures reach order-service and api-gateway",
            logs=(
                self._normal_background_logs(5)
                + sustained
            ),
            correlation_id=cid,
        ))

        # Step 5: recovery
        self._steps.append(ScenarioStep(
            step_index=5,
            phase="recovery",
            description="Network connectivity restored, traffic normalizes",
            logs=self._normal_background_logs(20),
        ))

    def _generate_intermittent_failures(
        self,
        source: str,
        target: str,
        correlation_id: str,
        count: int,
        failure_probability: float = 0.5,
    ) -> list[Any]:
        """Generate a mix of timeout warnings and success logs."""
        from .generator import LogEntry
        from .templates import render_log

        logs: list[Any] = []
        ts = datetime.now(timezone.utc)

        for i in range(count):
            if self._rng.random() < failure_probability:
                # Timeout / failure
                duration_ms = round(self._rng.uniform(3000, 10000), 2)
                retry = self._rng.randint(1, 3)
                message = (
                    f"Request to {target} timed out after {duration_ms}ms "
                    f"(retry {retry}/3)"
                )
                raw = (
                    f'{ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]}Z WARN {source}: '
                    f'downstream_timeout target={target} timeout={duration_ms}ms '
                    f'retry={retry}/3 correlation_id={correlation_id}'
                )
                logs.append(LogEntry(
                    timestamp=ts,
                    service_name=source,
                    level="warning",
                    message=message,
                    metadata={
                        "correlation_id": correlation_id,
                        "root_cause": False,
                        "propagated_symptom": True,
                        "error_type": "network_partition",
                        "target": target,
                        "duration_ms": duration_ms,
                        "retry": retry,
                    },
                    raw=raw,
                ))
            else:
                # Successful but slow
                node = self._topology.get_node(source)
                message_ok, raw_ok, meta_ok = render_log(
                    service_type=node.service_type,
                    service_name=source,
                    level="info",
                    rng=self._rng,
                    base_latency_ms=node.base_latency_ms * 2.0,
                    latency_jitter_ms=node.latency_jitter_ms * 2.0,
                )
                logs.append(LogEntry(
                    timestamp=ts,
                    service_name=source,
                    level="info",
                    message=message_ok,
                    metadata=meta_ok,
                    raw=raw_ok,
                ))
        return logs


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

SCENARIO_REGISTRY: dict[str, type[BaseScenario]] = {
    "database_pool_exhaustion": DatabasePoolExhaustionScenario,
    "auth_token_storm": AuthTokenStormScenario,
    "network_partition": NetworkPartitionScenario,
}


def list_scenarios() -> list[dict[str, str]]:
    """Return metadata for all registered scenarios."""
    return [
        {"name": cls.name, "description": cls.description}
        for cls in SCENARIO_REGISTRY.values()
    ]

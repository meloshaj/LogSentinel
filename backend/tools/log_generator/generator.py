"""Core payload generator that connects topology and templates.

The ``LogPayloadGenerator`` builds valid ``IngestPayload`` batches
that are directly compatible with the LogSentinel ``/ingest-log``
endpoint.  It uses the ``ServiceTopology`` to produce correlated
distributed traces and the template library to render realistic,
domain-specific log messages.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from .config import GeneratorConfig, default_ecommerce_topology
from .templates import render_log
from .topology import ServiceTopology
from .cascading_errors import CascadingExceptionEngine
from .scenarios import SCENARIO_REGISTRY, BaseScenario


# ---------------------------------------------------------------------------
# Pydantic schemas matching the backend's IngestPayload / LogEntry contract
# ---------------------------------------------------------------------------
# These are local mirrors so the generator can run as a standalone tool
# without importing from the main application.


class LogEntry(BaseModel):
    """Wire-compatible mirror of ``backend.app.main.LogEntry``."""

    timestamp: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    service_name: str = Field(..., min_length=1)
    level: str = Field(default="info", min_length=1)
    message: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: Optional[str] = Field(default=None)


class IngestPayload(BaseModel):
    """Wire-compatible mirror of ``backend.app.main.IngestPayload``."""

    source: str = Field(default="unknown", min_length=1)
    environment: str = Field(default="development", min_length=1)
    logs: list[LogEntry] = Field(..., min_length=1)
    correlation_id: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class LogPayloadGenerator:
    """Connects the topology DAG and the template library to produce
    valid ``IngestPayload`` batches.

    Parameters
    ----------
    config:
        A ``GeneratorConfig`` instance.  If ``None``, the default
        e-commerce topology is used.
    seed:
        Optional RNG seed for reproducible generation.
    """

    def __init__(
        self,
        config: GeneratorConfig | None = None,
        seed: int | None = None,
    ) -> None:
        self._config = config or default_ecommerce_topology()
        self._rng = random.Random(seed)
        self._topology = ServiceTopology(self._config.services)
        self._cascade_engine = CascadingExceptionEngine(self._topology, seed=seed)
        self._active_scenarios: dict[str, BaseScenario] = {}
        self._sequence: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_batch(self, size: int | None = None) -> IngestPayload:
        """Produce one ``IngestPayload`` with ``size`` log entries.

        The batch contains a probabilistic mix of:
        * **Distributed traces** — a correlated sequence of logs across
          the topology sharing a single ``correlation_id``.
        * **Independent logs** — individual service events selected
          randomly across the topology.

        If ``size`` is ``None``, ``config.batch_size`` is used.
        """
        size = size or self._config.batch_size
        logs: list[LogEntry] = []
        batch_correlation_id: str | None = None

        remaining = size

        # Optionally inject a full distributed trace.
        if self._rng.random() < self._config.trace_probability and remaining >= 3:
            trace_logs, trace_cid = self._generate_trace()
            logs.extend(trace_logs)
            batch_correlation_id = trace_cid
            remaining -= len(trace_logs)

        # Fill the rest with independent service events.
        while remaining > 0:
            entry = self._generate_single_log()
            logs.append(entry)
            remaining -= 1

        return IngestPayload(
            source=self._config.default_source,
            environment=self._config.environment,
            logs=logs,
            correlation_id=batch_correlation_id,
        )

    def generate_trace_batch(self) -> IngestPayload:
        """Produce one ``IngestPayload`` consisting entirely of a single
        correlated distributed trace through the full topology.
        """
        trace_logs, correlation_id = self._generate_trace()
        return IngestPayload(
            source=self._config.default_source,
            environment=self._config.environment,
            logs=trace_logs,
            correlation_id=correlation_id,
        )

    def generate_scenario_batch(
        self,
        scenario_name: str,
        step: int,
        background_noise: int = 10,
    ) -> IngestPayload:
        """Produce one ``IngestPayload`` for a specific scenario step,
        mixed with background noise traffic.

        Parameters
        ----------
        scenario_name:
            Key from ``SCENARIO_REGISTRY`` (e.g.
            ``"database_pool_exhaustion"``, ``"auth_token_storm"``,
            ``"network_partition"``).
        step:
            Zero-based step index in the scenario timeline.
        background_noise:
            Number of normal info-level logs to mix in alongside the
            scenario events, ensuring the backend parser must extract
            anomalies from realistic background traffic.

        Returns
        -------
        IngestPayload
            A single payload containing the scenario step's logs plus
            background noise.

        Raises
        ------
        ValueError
            If the scenario name is unknown or the step index is out of
            range.
        """
        if scenario_name not in SCENARIO_REGISTRY:
            raise ValueError(
                f"Unknown scenario '{scenario_name}'. "
                f"Available: {sorted(SCENARIO_REGISTRY)}"
            )

        # Lazily instantiate each scenario once and cache it.
        if scenario_name not in self._active_scenarios:
            scenario_cls = SCENARIO_REGISTRY[scenario_name]
            self._active_scenarios[scenario_name] = scenario_cls(
                config=self._config,
                seed=self._rng.randint(0, 2**31),
            )

        scenario = self._active_scenarios[scenario_name]

        if step < 0 or step >= len(scenario):
            raise ValueError(
                f"Step {step} out of range for scenario '{scenario_name}' "
                f"(0..{len(scenario) - 1})"
            )

        scenario_step = scenario.get_step(step)

        # Mix scenario logs with background noise.
        noise_logs: list[LogEntry] = []
        for _ in range(background_noise):
            noise_logs.append(self._generate_single_log())

        # Interleave: noise at the front, scenario events in the middle/end
        # so the backend must sift through normal traffic to find anomalies.
        all_logs = noise_logs + scenario_step.logs
        self._rng.shuffle(all_logs)

        return IngestPayload(
            source=self._config.default_source,
            environment=self._config.environment,
            logs=all_logs,
            correlation_id=scenario_step.correlation_id,
        )

    @property
    def topology(self) -> ServiceTopology:
        """Expose the underlying topology for introspection."""
        return self._topology

    @property
    def config(self) -> GeneratorConfig:
        """Expose the active configuration."""
        return self._config

    @property
    def cascade_engine(self) -> CascadingExceptionEngine:
        """Expose the cascading exception engine for direct use."""
        return self._cascade_engine

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pick_level(self, error_rate: float, warn_rate: float) -> str:
        """Select a severity level based on configured probabilities."""
        roll = self._rng.random()
        if roll < error_rate:
            return "error"
        if roll < error_rate + warn_rate:
            return "warning"
        return "info"

    def _generate_single_log(
        self,
        service_name: str | None = None,
        correlation_id: str | None = None,
        base_timestamp: datetime | None = None,
    ) -> LogEntry:
        """Generate one independent ``LogEntry``."""
        if service_name is None:
            service_name = self._rng.choice(self._topology.service_names)

        node = self._topology.get_node(service_name)
        level = self._pick_level(node.error_rate, node.warn_rate)

        message, raw, metadata = render_log(
            service_type=node.service_type,
            service_name=node.service_name,
            level=level,
            rng=self._rng,
            base_latency_ms=node.base_latency_ms,
            latency_jitter_ms=node.latency_jitter_ms,
        )

        if correlation_id:
            metadata["correlation_id"] = correlation_id

        self._sequence += 1
        metadata["seq"] = self._sequence

        timestamp = base_timestamp or datetime.now(timezone.utc)

        return LogEntry(
            timestamp=timestamp,
            service_name=node.service_name,
            level=level,
            message=message,
            metadata=metadata,
            raw=raw,
        )

    def _generate_trace(self) -> tuple[list[LogEntry], str]:
        """Generate a correlated trace across the full topology.

        Returns ``(log_entries, correlation_id)``.
        """
        roots = self._topology.root_services()
        entry = self._rng.choice(roots) if roots else self._topology.service_names[0]
        correlation_id, trace_path = self._topology.generate_trace_path(entry)

        logs: list[LogEntry] = []
        base_ts = datetime.now(timezone.utc)

        for idx, service_name in enumerate(trace_path):
            # Simulate time progression through the trace.
            node = self._topology.get_node(service_name)
            hop_offset = timedelta(
                milliseconds=idx * node.base_latency_ms
                + self._rng.uniform(0, node.latency_jitter_ms),
            )
            entry_ts = base_ts + hop_offset

            entry_log = self._generate_single_log(
                service_name=service_name,
                correlation_id=correlation_id,
                base_timestamp=entry_ts,
            )
            logs.append(entry_log)

        return logs, correlation_id

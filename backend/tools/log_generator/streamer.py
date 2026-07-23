"""Async HTTP streaming engine with rate-limiting and backpressure.

``LogStreamer`` drives generated payloads into the LogSentinel
``/ingest-log`` endpoint using ``httpx.AsyncClient`` with connection
pooling.  It handles HTTP 429 / 503 with exponential backoff+jitter
and maintains real-time execution telemetry.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict
from typing import Any

import httpx

from .generator import IngestPayload

logger = logging.getLogger("logsentinel.log_generator.streamer")


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class StreamerTelemetry:
    """Lightweight real-time telemetry accumulator."""

    def __init__(self) -> None:
        self.total_logs_sent: int = 0
        self.total_batches_sent: int = 0
        self.successful_batches: int = 0
        self.failed_batches: int = 0
        self.retried_batches: int = 0
        self.status_distribution: dict[int, int] = defaultdict(int)
        self.latencies_ms: list[float] = []
        self._start_time: float = time.monotonic()

    def record_success(self, log_count: int, status: int, latency_ms: float) -> None:
        self.total_logs_sent += log_count
        self.total_batches_sent += 1
        self.successful_batches += 1
        self.status_distribution[status] += 1
        self.latencies_ms.append(latency_ms)

    def record_failure(self, status: int | None, latency_ms: float) -> None:
        self.total_batches_sent += 1
        self.failed_batches += 1
        if status is not None:
            self.status_distribution[status] += 1
        self.latencies_ms.append(latency_ms)

    def record_retry(self) -> None:
        self.retried_batches += 1

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return sum(self.latencies_ms) / len(self.latencies_ms)

    @property
    def throughput_logs_per_sec(self) -> float:
        elapsed = self.elapsed_seconds
        return self.total_logs_sent / elapsed if elapsed > 0 else 0.0

    @property
    def success_rate_pct(self) -> float:
        total = self.total_batches_sent
        return (self.successful_batches / total * 100.0) if total > 0 else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "total_logs_sent": self.total_logs_sent,
            "total_batches": self.total_batches_sent,
            "successful_batches": self.successful_batches,
            "failed_batches": self.failed_batches,
            "retried_batches": self.retried_batches,
            "success_rate_pct": round(self.success_rate_pct, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "throughput_logs_per_sec": round(self.throughput_logs_per_sec, 1),
            "status_distribution": dict(self.status_distribution),
        }

    def format_live_line(self) -> str:
        """Return a compact one-line status string for terminal display."""
        return (
            f"  Logs: {self.total_logs_sent:>8,d} | "
            f"Batches: {self.successful_batches}/{self.total_batches_sent} | "
            f"Rate: {self.throughput_logs_per_sec:>7.1f} logs/s | "
            f"Latency: {self.avg_latency_ms:>6.1f}ms | "
            f"Errors: {self.failed_batches} | "
            f"Retries: {self.retried_batches}"
        )

    def format_final_report(self) -> str:
        """Return a formatted multi-line summary table."""
        lines = [
            "",
            "=" * 64,
            "  LogSentinel Mock Log-Generator -- Final Report",
            "=" * 64,
            f"  Duration            : {self.elapsed_seconds:>10.2f} s",
            f"  Total Logs Sent     : {self.total_logs_sent:>10,d}",
            f"  Total Batches       : {self.total_batches_sent:>10,d}",
            f"  Successful Batches  : {self.successful_batches:>10,d}",
            f"  Failed Batches      : {self.failed_batches:>10,d}",
            f"  Retried Batches     : {self.retried_batches:>10,d}",
            f"  Success Rate        : {self.success_rate_pct:>9.1f} %",
            f"  Avg Latency         : {self.avg_latency_ms:>10.2f} ms",
            f"  Throughput          : {self.throughput_logs_per_sec:>10.1f} logs/s",
            "-" * 64,
            "  HTTP Status Distribution:",
        ]
        for status, count in sorted(self.status_distribution.items()):
            lines.append(f"    {status}: {count:>6,d}")
        lines.append("=" * 64)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Streamer
# ---------------------------------------------------------------------------

_RETRYABLE_STATUSES = {429, 503}
_MAX_RETRIES = 5
_BASE_BACKOFF_S = 0.5
_MAX_BACKOFF_S = 30.0


class LogStreamer:
    """Async HTTP streaming engine for the mock log generator.

    Parameters
    ----------
    target_url:
        Full URL of the ``/ingest-log`` endpoint.
    api_key:
        Optional API key sent as ``X-API-Key`` header.
    timeout_s:
        Per-request timeout in seconds.
    max_connections:
        ``httpx.AsyncClient`` connection pool limit.
    """

    def __init__(
        self,
        target_url: str = "http://localhost:8000/ingest-log",
        api_key: str | None = None,
        timeout_s: float = 15.0,
        max_connections: int = 20,
    ) -> None:
        self._target_url = target_url
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_s, connect=5.0)
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_connections,
        )
        self._client: httpx.AsyncClient | None = None
        self._rng = random.Random()
        self.telemetry = StreamerTelemetry()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._api_key:
                headers["X-API-Key"] = self._api_key
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=self._timeout,
                limits=self._limits,
            )
        return self._client

    async def close(self) -> None:
        """Gracefully close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def send_batch(self, payload: IngestPayload) -> bool:
        """Send a single IngestPayload with retry on 429/503.

        Returns ``True`` on success, ``False`` on terminal failure.
        """
        client = await self._ensure_client()
        body = payload.model_dump(mode="json")
        log_count = len(payload.logs)

        for attempt in range(1, _MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                resp = await client.post(self._target_url, json=body)
                latency_ms = (time.perf_counter() - start) * 1000.0

                if resp.status_code in (200, 201, 202):
                    self.telemetry.record_success(log_count, resp.status_code, latency_ms)
                    return True

                if resp.status_code in _RETRYABLE_STATUSES and attempt < _MAX_RETRIES:
                    self.telemetry.record_retry()
                    backoff = self._backoff(attempt)
                    logger.warning(
                        "HTTP %d from %s -- retrying in %.1fs (attempt %d/%d)",
                        resp.status_code, self._target_url, backoff, attempt, _MAX_RETRIES,
                    )
                    await asyncio.sleep(backoff)
                    continue

                # Non-retryable or exhausted retries.
                self.telemetry.record_failure(resp.status_code, latency_ms)
                logger.error(
                    "HTTP %d from %s (attempt %d/%d) -- batch dropped",
                    resp.status_code, self._target_url, attempt, _MAX_RETRIES,
                )
                return False

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                latency_ms = (time.perf_counter() - start) * 1000.0
                if attempt < _MAX_RETRIES:
                    self.telemetry.record_retry()
                    backoff = self._backoff(attempt)
                    logger.warning(
                        "%s connecting to %s -- retrying in %.1fs (attempt %d/%d)",
                        type(exc).__name__, self._target_url, backoff, attempt, _MAX_RETRIES,
                    )
                    await asyncio.sleep(backoff)
                    continue

                self.telemetry.record_failure(None, latency_ms)
                logger.error(
                    "%s connecting to %s -- batch dropped after %d attempts",
                    type(exc).__name__, self._target_url, _MAX_RETRIES,
                )
                return False

            except Exception as exc:
                latency_ms = (time.perf_counter() - start) * 1000.0
                self.telemetry.record_failure(None, latency_ms)
                logger.error("Unexpected error sending batch: %s: %s", type(exc).__name__, exc)
                return False

        return False  # pragma: no cover

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        ceiling = min(_BASE_BACKOFF_S * (2 ** (attempt - 1)), _MAX_BACKOFF_S)
        return self._rng.uniform(0, ceiling)

    # ------------------------------------------------------------------
    # High-level streaming loops
    # ------------------------------------------------------------------

    async def stream_continuous(
        self,
        generator: Any,
        rate_logs_per_sec: float = 100.0,
        batch_size: int = 50,
        duration_seconds: float | None = None,
        telemetry_interval: float = 5.0,
    ) -> None:
        """Stream continuous background traffic with rate-limiting.

        Parameters
        ----------
        generator:
            A ``LogPayloadGenerator`` instance.
        rate_logs_per_sec:
            Target sustained throughput.
        batch_size:
            Logs per batch.
        duration_seconds:
            Total run duration.  ``None`` = run until cancelled.
        telemetry_interval:
            Seconds between live telemetry prints.
        """
        interval_per_batch = batch_size / rate_logs_per_sec if rate_logs_per_sec > 0 else 0.1
        deadline = time.monotonic() + duration_seconds if duration_seconds else None
        last_report = time.monotonic()

        print(f"\n  Streaming at ~{rate_logs_per_sec:.0f} logs/s "
              f"(batch_size={batch_size})"
              f"{f', duration={duration_seconds}s' if duration_seconds else ', Ctrl+C to stop'}")
        print("-" * 64)

        try:
            while True:
                if deadline and time.monotonic() >= deadline:
                    break

                batch_start = time.monotonic()
                payload = generator.generate_batch(size=batch_size)
                await self.send_batch(payload)

                # Live telemetry display.
                if time.monotonic() - last_report >= telemetry_interval:
                    print(f"\r{self.telemetry.format_live_line()}", flush=True)
                    last_report = time.monotonic()

                # Rate-limiting sleep.
                elapsed = time.monotonic() - batch_start
                sleep_time = max(0.0, interval_per_batch - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            pass

        print(self.telemetry.format_final_report())

    async def stream_scenario(
        self,
        generator: Any,
        scenario_name: str,
        step_duration_seconds: float = 15.0,
        background_noise: int = 10,
        batch_size: int = 50,
        telemetry_interval: float = 5.0,
    ) -> None:
        """Execute a pre-packaged stress scenario step-by-step.

        Each scenario has 6 steps.  Between scenario batches, background
        traffic is generated to maintain realism.
        """
        from .scenarios import SCENARIO_REGISTRY

        if scenario_name not in SCENARIO_REGISTRY:
            print(f"  ERROR: Unknown scenario '{scenario_name}'.")
            print(f"  Available: {sorted(SCENARIO_REGISTRY)}")
            return

        scenario_cls = SCENARIO_REGISTRY[scenario_name]
        total_steps = 6  # All scenarios are 6 steps

        print(f"\n  Scenario: {scenario_name}")
        print(f"  Steps: {total_steps}, Step Duration: {step_duration_seconds}s")
        print(f"  Background noise: {background_noise} logs per batch")
        print("-" * 64)

        try:
            for step_idx in range(total_steps):
                # Emit the scenario step batch.
                scenario_batch = generator.generate_scenario_batch(
                    scenario_name, step=step_idx, background_noise=background_noise,
                )
                step_phase = ""
                if scenario_name in generator._active_scenarios:
                    sc = generator._active_scenarios[scenario_name]
                    step_obj = sc.get_step(step_idx)
                    step_phase = step_obj.phase

                print(f"\n  >> Step {step_idx}/{total_steps - 1}: "
                      f"{step_phase} ({len(scenario_batch.logs)} scenario logs)")

                await self.send_batch(scenario_batch)

                # Fill the remaining step duration with background traffic.
                step_deadline = time.monotonic() + step_duration_seconds
                last_report = time.monotonic()

                while time.monotonic() < step_deadline:
                    bg_batch = generator.generate_batch(size=batch_size)
                    await self.send_batch(bg_batch)

                    if time.monotonic() - last_report >= telemetry_interval:
                        print(f"\r{self.telemetry.format_live_line()}", flush=True)
                        last_report = time.monotonic()

                    await asyncio.sleep(0.2)

        except asyncio.CancelledError:
            pass

        print(self.telemetry.format_final_report())

    async def burst(
        self,
        generator: Any,
        total_logs: int = 10000,
        batch_size: int = 50,
        concurrency: int = 5,
    ) -> None:
        """Send a rapid un-rate-limited burst of logs.

        Uses ``concurrency`` parallel workers to maximize throughput and
        stress-test the backend's ``AsyncLogBuffer`` queue limits.
        """
        total_batches = max(1, total_logs // batch_size)
        remaining = asyncio.Queue[int](maxsize=total_batches + concurrency)

        for i in range(total_batches):
            await remaining.put(i)

        # Sentinel values to stop workers.
        for _ in range(concurrency):
            await remaining.put(-1)

        print(f"\n  Burst: {total_logs} logs in {total_batches} batches "
              f"({concurrency} workers, batch_size={batch_size})")
        print("-" * 64)

        async def worker() -> None:
            while True:
                idx = await remaining.get()
                if idx < 0:
                    return
                payload = generator.generate_batch(size=batch_size)
                await self.send_batch(payload)

        try:
            await asyncio.gather(*(worker() for _ in range(concurrency)))
        except asyncio.CancelledError:
            pass

        print(self.telemetry.format_final_report())

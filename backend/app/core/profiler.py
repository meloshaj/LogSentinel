"""Database profiling and benchmarking subsystem."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np
from sqlalchemy import event
from sqlalchemy.engine import Engine, ExecutionContext

logger = logging.getLogger("logsentinel.profiler")


class DatabaseProfiler:
    """Lightweight profiling subsystem for database batch execution."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.enabled = False

        # Query level metrics
        self.query_durations_ms: list[float] = []
        self.slow_query_threshold_ms = 200.0

        # Batch level metrics
        self.batch_durations_ms: list[float] = []
        self.batch_sizes: list[int] = []

        # Context-local timer for queries
        self._query_timers: dict[Any, float] = {}

    def attach_to_engine(self, engine: Engine) -> None:
        """Attach profiling hooks to the SQLAlchemy engine."""
        self.enabled = True

        @event.listens_for(engine, "before_cursor_execute")
        def before_cursor_execute(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: ExecutionContext,
            executemany: bool,
        ) -> None:
            self._query_timers[context] = time.perf_counter()

        @event.listens_for(engine, "after_cursor_execute")
        def after_cursor_execute(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: ExecutionContext,
            executemany: bool,
        ) -> None:
            start_time = self._query_timers.pop(context, None)
            if start_time is None:
                return

            duration_ms = (time.perf_counter() - start_time) * 1000.0

            with self.lock:
                self.query_durations_ms.append(duration_ms)

            if duration_ms > self.slow_query_threshold_ms:
                logger.warning(
                    "Slow query detected: %.2f ms (>%.2f ms) - %s",
                    duration_ms,
                    self.slow_query_threshold_ms,
                    statement[:200],
                )

        @event.listens_for(engine, "handle_error")
        def handle_error(context: Any) -> None:
            if hasattr(context, "execution_context") and context.execution_context:
                self._query_timers.pop(context.execution_context, None)

        logger.info("Database batch profiling hooks attached to engine.")

    def track_batch(self, batch_size: int, duration_ms: float) -> None:
        """Track high-level batch latency and dimensions."""
        if not self.enabled:
            return

        with self.lock:
            self.batch_sizes.append(batch_size)
            self.batch_durations_ms.append(duration_ms)

    def get_profiling_summary(self) -> dict[str, Any]:
        """Calculate and return percentile latency and throughput stats."""
        if not self.enabled:
            return {"enabled": False}

        with self.lock:
            queries = self.query_durations_ms.copy()
            batches = self.batch_durations_ms.copy()
            sizes = self.batch_sizes.copy()

        if not queries and not batches:
            return {"enabled": True, "message": "No profiling data available yet"}

        stats: dict[str, Any] = {"enabled": True}

        if queries:
            q_arr = np.array(queries)
            stats["queries"] = {
                "count": len(queries),
                "avg_ms": round(float(np.mean(q_arr)), 2),
                "min_ms": round(float(np.min(q_arr)), 2),
                "max_ms": round(float(np.max(q_arr)), 2),
                "p50_ms": round(float(np.percentile(q_arr, 50)), 2),
                "p95_ms": round(float(np.percentile(q_arr, 95)), 2),
                "p99_ms": round(float(np.percentile(q_arr, 99)), 2),
            }

        if batches and sizes:
            b_arr = np.array(batches)
            s_arr = np.array(sizes)
            total_records = int(np.sum(s_arr))
            total_duration_ms = float(np.sum(b_arr))

            throughput = (
                (total_records / total_duration_ms) * 1000.0
                if total_duration_ms > 0
                else 0.0
            )

            stats["batches"] = {
                "count": len(batches),
                "total_records": total_records,
                "avg_batch_size": round(float(np.mean(s_arr)), 2),
                "avg_duration_ms": round(float(np.mean(b_arr)), 2),
                "min_duration_ms": round(float(np.min(b_arr)), 2),
                "max_duration_ms": round(float(np.max(b_arr)), 2),
                "p50_duration_ms": round(float(np.percentile(b_arr, 50)), 2),
                "p95_duration_ms": round(float(np.percentile(b_arr, 95)), 2),
                "throughput_records_per_sec": round(throughput, 2),
            }

        return stats

    def reset(self) -> None:
        """Clear all accumulated metrics."""
        with self.lock:
            self.query_durations_ms.clear()
            self.batch_durations_ms.clear()
            self.batch_sizes.clear()


db_profiler = DatabaseProfiler()

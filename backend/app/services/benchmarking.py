import logging
import time
from collections import deque
from typing import Any

from ..models import PerformanceEvent

logger = logging.getLogger("logsentinel.benchmarking")

class BenchmarkingCollector:
    """O(1) memory metrics collector for tracking system performance health."""
    
    def __init__(
        self,
        ema_alpha: float = 0.1,
        latency_threshold_ms: float = 500.0,
        queue_depth_threshold_ratio: float = 0.8,
        db_batch_threshold_ms: float = 2000.0,
    ):
        self.ema_alpha = ema_alpha
        
        # Thresholds
        self.latency_threshold_ms = latency_threshold_ms
        self.queue_depth_threshold_ratio = queue_depth_threshold_ratio
        self.db_batch_threshold_ms = db_batch_threshold_ms
        
        self.event_manager = None  # Will be injected
        
        # State
        self._ema_latency_ms: float = 0.0
        self._ema_db_batch_ms: float = 0.0
        
        self._queue_depth: int = 0
        self._max_queue_depth: int = 10000
        
        self._throughput_window = deque(maxlen=60)
        self._current_second = int(time.time())
        self._current_second_count = 0
        
        # Rate limiting alerts to avoid flooding
        self._last_alert_time: dict[str, float] = {}
        self._alert_cooldown_seconds = 10.0
        
    def bind_event_manager(self, event_manager: Any):
        """Bind the event manager after initialization."""
        self.event_manager = event_manager
        
    def _update_throughput_window(self):
        """Rotate the throughput window if the second has advanced."""
        now = int(time.time())
        if now > self._current_second:
            diff = now - self._current_second
            self._throughput_window.append(self._current_second_count)
            # Fill gaps if more than 1 second passed (up to maxlen to avoid huge loops)
            for _ in range(min(diff - 1, 60)):
                self._throughput_window.append(0)
            
            self._current_second = now
            self._current_second_count = 0

    def record_ingestion(self, count: int = 1):
        """Record ingested logs for throughput calculation."""
        self._update_throughput_window()
        self._current_second_count += count

    def record_latency(self, latency_ms: float):
        """Record pipeline processing latency with EMA."""
        if self._ema_latency_ms == 0.0:
            self._ema_latency_ms = latency_ms
        else:
            self._ema_latency_ms = (self.ema_alpha * latency_ms) + ((1 - self.ema_alpha) * self._ema_latency_ms)
        
        if self._ema_latency_ms > self.latency_threshold_ms:
            self._trigger_event("pipeline_latency", self._ema_latency_ms, self.latency_threshold_ms)

    def set_queue_depth(self, depth: int, max_depth: int = 10000):
        """Record current queue depth and check backpressure."""
        self._queue_depth = depth
        self._max_queue_depth = max_depth
        
        ratio = depth / max(1, max_depth)
        if ratio > self.queue_depth_threshold_ratio:
            self._trigger_event("queue_backpressure", ratio, self.queue_depth_threshold_ratio)

    def record_db_batch_duration(self, duration_ms: float):
        """Record DB batch write duration with EMA."""
        if self._ema_db_batch_ms == 0.0:
            self._ema_db_batch_ms = duration_ms
        else:
            self._ema_db_batch_ms = (self.ema_alpha * duration_ms) + ((1 - self.ema_alpha) * self._ema_db_batch_ms)
            
        if self._ema_db_batch_ms > self.db_batch_threshold_ms:
            self._trigger_event("db_batch_delay", self._ema_db_batch_ms, self.db_batch_threshold_ms)

    def get_health_metrics(self) -> dict[str, Any]:
        """Return a snapshot of current performance metrics."""
        self._update_throughput_window()
        throughput = sum(self._throughput_window) / max(1, len(self._throughput_window)) if self._throughput_window else 0.0
        
        return {
            "throughput_logs_per_sec": round(throughput, 2),
            "pipeline_latency_ms": round(self._ema_latency_ms, 2),
            "queue_depth": self._queue_depth,
            "queue_capacity_percent": round((self._queue_depth / max(1, self._max_queue_depth)) * 100, 2),
            "db_batch_duration_ms": round(self._ema_db_batch_ms, 2)
        }

    def _trigger_event(self, metric_name: str, current_value: float, threshold: float):
        """Create and enqueue a PerformanceEvent to the event manager."""
        if not self.event_manager:
            return
            
        now = time.time()
        last_time = self._last_alert_time.get(metric_name, 0.0)
        
        # Throttle alerts for the same metric
        if now - last_time < self._alert_cooldown_seconds:
            return
            
        self._last_alert_time[metric_name] = now
            
        event = PerformanceEvent(
            metric_name=metric_name,
            current_value=current_value,
            threshold=threshold,
            severity="error" if "delay" in metric_name or "backpressure" in metric_name else "warning",
            health_metrics=self.get_health_metrics()
        )
        
        if hasattr(self.event_manager, 'enqueue_performance_event'):
            self.event_manager.enqueue_performance_event(event)

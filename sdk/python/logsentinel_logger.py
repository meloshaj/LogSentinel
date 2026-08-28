import atexit
import json
import logging
import queue
import sys
import threading
import time
import traceback
import urllib.request
from typing import Any, Dict, List, Optional


class LogSentinelHandler(logging.Handler):
    """
    A zero-dependency Python logging handler that batches and streams logs to LogSentinel.
    Uses a daemon thread to send logs via HTTP in the background to ensure the main
    application loop is never blocked.
    """

    def __init__(
        self,
        api_key: str,
        service_name: str,
        endpoint: str = "http://localhost:8000/api/v1/ingest/bulk",
        batch_size: int = 100,
        flush_interval_seconds: float = 1.0,
        max_queue_size: int = 10000,
    ):
        """
        Args:
            api_key: The X-API-Key required by the LogSentinel ingestion gateway.
            service_name: The name of the emitting service.
            endpoint: The LogSentinel bulk ingestion endpoint.
            batch_size: Flush when the internal buffer reaches this size.
            flush_interval_seconds: Flush periodically on this interval if buffer is not full.
            max_queue_size: Maximum logs to keep in memory before dropping new logs.
        """
        super().__init__()
        self.api_key = api_key
        self.service_name = service_name
        self.endpoint = endpoint
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds
        self.max_queue_size = max_queue_size

        self.queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self.drop_count = 0
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._worker, daemon=True, name="LogSentinelHandlerWorker"
        )
        self._worker_thread.start()

        # Ensure remaining logs are flushed upon application exit
        atexit.register(self.close)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 1. Format the base message
            message = self.format(record)

            # 2. Serialize exception traces
            if record.exc_info and not record.exc_text:
                record.exc_text = "".join(traceback.format_exception(*record.exc_info))

            if record.exc_text and record.exc_text not in message:
                message = f"{message}\n{record.exc_text}"

            # 3. Construct canonical log payload
            log_entry = {
                "timestamp": record.created,
                "level": record.levelname,
                "message": message,
                "service_name": self.service_name,
            }

            # 4. Extract tracing context
            trace_id = getattr(record, "trace_id", None)
            if trace_id is not None:
                log_entry["trace_id"] = str(trace_id)

            span_id = getattr(record, "span_id", None)
            if span_id is not None:
                log_entry["span_id"] = str(span_id)

            parent_span_id = getattr(record, "parent_span_id", None)
            if parent_span_id is not None:
                log_entry["parent_span_id"] = str(parent_span_id)

            # 5. Extract arbitrary metadata
            # Exclude standard logging fields to avoid polluting the metadata dict
            standard_keys = {
                "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "created", "msecs", "relativeCreated", "thread", "threadName",
                "processName", "process", "message", "asctime", "trace_id",
                "span_id", "parent_span_id", "taskName", "metadata", "color_message"
            }

            metadata: Dict[str, Any] = getattr(record, "metadata", {})
            if not isinstance(metadata, dict):
                metadata = {"value": str(metadata)}

            for key, val in record.__dict__.items():
                if key not in standard_keys and not key.startswith("_"):
                    try:
                        # Test JSON serializability
                        json.dumps(val)
                        metadata[key] = val
                    except (TypeError, ValueError):
                        metadata[key] = str(val)

            if metadata:
                log_entry["metadata"] = metadata

            # 6. Enqueue log entry
            self.queue.put_nowait(log_entry)

        except queue.Full:
            self.drop_count += 1
            if self.drop_count % 100 == 1:
                sys.stderr.write(
                    f"LogSentinelHandler: Queue full (size={self.max_queue_size}). "
                    f"Dropped {self.drop_count} logs so far.\n"
                )
        except Exception:
            self.handleError(record)

    def _worker(self) -> None:
        batch = []
        last_flush_time = time.time()

        while not self._stop_event.is_set():
            try:
                # Wait for the next item or timeout based on flush interval
                time_since_flush = time.time() - last_flush_time
                timeout = max(0.0, self.flush_interval_seconds - time_since_flush)
                item = self.queue.get(timeout=timeout)
                batch.append(item)
            except queue.Empty:
                pass
            except Exception as e:
                sys.stderr.write(f"LogSentinelHandler: Worker error: {e}\n")

            now = time.time()
            if len(batch) >= self.batch_size or (batch and now - last_flush_time >= self.flush_interval_seconds):
                self._send_batch(batch)
                batch = []
                last_flush_time = time.time()

        # Process termination: flush all remaining logs in the queue
        while not self.queue.empty():
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break

        if batch:
            for i in range(0, len(batch), self.batch_size):
                self._send_batch(batch[i : i + self.batch_size])

    def _send_batch(self, batch: List[Dict[str, Any]]) -> None:
        if not batch:
            return

        payload = {"logs": batch}
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
                "X-Service-Name": self.service_name,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=5.0) as response:
                if response.status not in (200, 202):
                    sys.stderr.write(
                        f"LogSentinelHandler: Received unexpected status {response.status} from ingestion endpoint.\n"
                    )
        except Exception as e:
            self.drop_count += len(batch)
            sys.stderr.write(
                f"LogSentinelHandler: Failed to send {len(batch)} logs ({e}). "
                f"Total dropped: {self.drop_count}\n"
            )

    def close(self) -> None:
        if not self._stop_event.is_set():
            self._stop_event.set()
            if self._worker_thread.is_alive():
                # Allow a short grace period for the worker thread to finish flushing
                self._worker_thread.join(timeout=2.0)
        super().close()


if __name__ == "__main__":
    import uuid
    import random

    # 1. Setup Logging
    demo_logger = logging.getLogger("demo_service")
    demo_logger.setLevel(logging.DEBUG)

    # 2. Configure the custom LogSentinelHandler
    # Change endpoint to match your local setup, e.g., if behind a different port
    logsentinel_handler = LogSentinelHandler(
        api_key="dev-local-key",  # Matches the INGEST_API_KEY in docker-compose.yml
        service_name="payment-gateway",
        endpoint="http://localhost:8000/api/v1/ingest/bulk",
        batch_size=10,        # Small batch size for demo purposes
        flush_interval_seconds=1.0
    )
    
    # Optional console handler to view output locally
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    
    demo_logger.addHandler(logsentinel_handler)
    demo_logger.addHandler(console_handler)

    # 3. Emit Normal Logs
    print("Emitting 50 normal logs...")
    for i in range(50):
        # Additional fields are passed via the standard `extra` dictionary.
        # They will automatically be parsed and included in the LogSentinel metadata.
        trace_id = str(uuid.uuid4())
        demo_logger.info(
            f"Processed transaction {i}",
            extra={
                "trace_id": trace_id,
                "span_id": str(uuid.uuid4())[:8],
                "transaction_amount": round(random.uniform(10.0, 500.0), 2),
                "currency": "USD"
            }
        )
        # Small delay to simulate processing time
        time.sleep(0.01)

    # 4. Emit Error Logs
    print("\nEmitting 5 simulated error logs...")
    for i in range(5):
        trace_id = str(uuid.uuid4())
        try:
            # Simulate a crash
            raise ValueError("Manual crash for demo purposes")
        except Exception:
            demo_logger.error(
                "Critical division by zero during payment calculation",
                exc_info=True,
                extra={
                    "trace_id": trace_id,
                    "customer_id": f"cust_{random.randint(1000, 9999)}"
                }
            )

    print("\nLogs generated. Waiting 2 seconds for background thread to flush...")
    time.sleep(2.0)
    print("Demo complete! Verify ingestion on your LogSentinel server or Redis stream.")

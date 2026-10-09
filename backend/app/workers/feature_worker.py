"""Async worker for sliding window feature extraction from parsed logs.

This worker runs independently from the Drain3 parsing pipeline and extracts
features from log windows for downstream anomaly detection.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.pipeline_identity import logical_telemetry_id
from ..ml.anomaly_detector import IsolationForestAnomalyDetector
from ..ml.feature_extractor import (
    SlidingWindowFeatureExtractor as SlidingWindowExtractor,
)
from ..ml.feature_extractor import WindowConfig
from ..ml.model_registry import load_active_detector, load_registered_detector
from ..models import FeatureVector, ParsedLog
from ..observability.metrics import FEATURE_FAILURES_TOTAL, FEATURE_RETRIES_TOTAL
from ..repositories.feature_repository import FeatureRepository
from ..security.redaction import sanitize_error_text
from ..security.tenant_boundary import TenantBoundaryViolation
from ..services.durable_queue import DurableQueue
from ..services.telemetry import telemetry_event, telemetry_manager
from .event_manager import EventManager

logger = logging.getLogger("logsentinel.feature_worker")


class FeatureExtractionWorker:
    """Background worker that generates features from parsed log streams.

    This worker:
    1. Receives parsed logs from the Drain3 pipeline
    2. Buffers them in a sliding window extractor
    3. Periodically generates windows and extracts features
    4. Stores feature vectors for downstream ML processing
    """

    def __init__(
        self,
        window_config: WindowConfig | None = None,
        extraction_interval_seconds: float = 10.0,
        feature_buffer_size: int = 1000,
        anomaly_detector: IsolationForestAnomalyDetector | None = None,
        anomaly_model_path: str | Path | None = None,
        feature_repository: FeatureRepository | None = None,
        event_manager: EventManager | None = None,
    ) -> None:
        """Initialize the feature extraction worker.

        Args:
            window_config: Sliding window configuration
            extraction_interval_seconds: How often to generate windows
            feature_buffer_size: Max features to keep in memory
        """
        self.window_config = window_config or WindowConfig()
        self.extraction_interval_seconds = extraction_interval_seconds

        self.extractor = SlidingWindowExtractor(self.window_config)
        # A single process can consume records for multiple tenants. Keep a
        # separate time-window state per tenant and owner so evidence and
        # anomaly scores can never mix users in the same tenant.
        self._extractors: dict[tuple[str, int], SlidingWindowExtractor] = {
            ("default", 0): self.extractor
        }
        self.anomaly_model_path = (
            Path(anomaly_model_path) if anomaly_model_path is not None else None
        )
        self.model_load_error: str | None = None
        self.anomaly_detector = self._resolve_anomaly_detector(
            anomaly_detector, self.anomaly_model_path
        )
        self._anomaly_detectors: dict[
            tuple[str, int], IsolationForestAnomalyDetector
        ] = {}
        self._model_versions: dict[tuple[str, int], str] = {}
        self._model_checked_at: dict[tuple[str, int], float] = {}
        if self.anomaly_detector is not None:
            self._anomaly_detectors[("default", 0)] = self.anomaly_detector
        self._feature_repository = feature_repository
        self.event_manager = event_manager
        durable_engine = getattr(feature_repository, "_engine", None)
        self._contribution_queue = (
            DurableQueue(
                engine=durable_engine,
                max_attempts=5,
                timeout=60,
                success_status="completed",
                terminal_status="failed",
            )
            if feature_repository is not None
            else None
        )
        self._feature_work_queue = (
            DurableQueue(
                engine=durable_engine,
                max_attempts=5,
                timeout=60,
                success_status="completed",
                terminal_status="failed",
            )
            if feature_repository is not None
            else None
        )
        self._last_feature_work_vector: FeatureVector | None = None
        self._feature_retries = 0
        self._feature_failures = 0

        # Buffer recent feature vectors for inspection/debugging
        self._feature_buffer: deque[FeatureVector] = deque(maxlen=feature_buffer_size)

        # Worker state
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._features_extracted = 0
        self._extraction_errors = 0
        self._last_extraction_at: str | None = None

        logger.info(
            "FeatureExtractionWorker initialized: interval=%ds window=%ds stride=%ds",
            self.extraction_interval_seconds,
            self.window_config.window_size_seconds,
            self.window_config.stride_seconds,
        )

    def start(self) -> None:
        """Start the background feature extraction loop."""
        if self._task and not self._task.done():
            logger.warning("FeatureExtractionWorker already running")
            return

        self._running = True
        self._task = asyncio.create_task(self.run(), name="feature-extraction-worker")
        logger.info("FeatureExtractionWorker started")

    async def stop(self) -> None:
        """Stop the background worker cleanly."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

        logger.info("FeatureExtractionWorker stopped")

    async def run(self) -> None:
        """Main worker loop that periodically extracts features."""
        while self._running:
            try:
                await self.process_durable_work(max_items=100)
                await asyncio.sleep(self.extraction_interval_seconds)
                await self.extract_pending_features()
                await self.process_durable_work(max_items=100)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._extraction_errors += 1
                logger.error(
                    "Feature extraction worker encountered an error exception_type=%s detail=%s",
                    type(exc).__name__,
                    sanitize_error_text(exc),
                )

    async def extract_pending_features(
        self, current_time: datetime | None = None
    ) -> list[FeatureVector]:
        """Generate all pending windows and extract features.

        Returns:
            List of extracted FeatureVector objects
        """
        try:
            if self._feature_repository is not None:
                await self.process_durable_work(max_items=100)
                await self._reconcile_recent_feature_inputs()

            windows_by_scope = {
                scope_key: extractor.peek_pending_windows(current_time=current_time)
                for scope_key, extractor in self._extractors.items()
            }
            pending_windows = [
                (scope_key, window)
                for scope_key, windows in windows_by_scope.items()
                for window in windows
            ]

            if not pending_windows:
                # A cursor that crossed only below-threshold windows is safe
                # to advance: there is no durable feature work to register.
                for extractor in self._extractors.values():
                    extractor.commit_pending_windows()
                return []

            features: list[FeatureVector] = []

            for scope_key, window in pending_windows:
                tenant_id, owner_user_id = scope_key
                feature_vector = self._extractors[scope_key].extract_features(window)
                if str(tenant_id).strip() != str(feature_vector.tenant_id).strip():
                    raise TenantBoundaryViolation(
                        "tenant identity mismatch at feature-worker"
                    )
                if owner_user_id != feature_vector.owner_user_id:
                    raise TenantBoundaryViolation(
                        "owner identity mismatch at feature-worker"
                    )
                try:
                    detector = await self._detector_for_tenant(tenant_id, owner_user_id)
                    if detector is not None and detector.model is not None:
                        feature_vector.anomaly_prediction = await asyncio.to_thread(
                            detector.predict, feature_vector
                        )
                except Exception as exc:
                    logger.error(
                        "Failed to run anomaly prediction for window %s exception_type=%s detail=%s",
                        window.window_id,
                        type(exc).__name__,
                        sanitize_error_text(exc),
                    )
                features.append(feature_vector)

            # A closed window is not consumed until its durable work row is
            # registered. The registration is conflict-safe by tenant/window
            # identity, so repeating this block is safe after a crash.
            if self._feature_repository is not None and features:
                await self._feature_repository.register_feature_work(features)

            for scope_key, extractor in self._extractors.items():
                if scope_key in windows_by_scope:
                    extractor.commit_pending_windows(windows_by_scope[scope_key])

            self._feature_buffer.extend(features)
            self._features_extracted += len(features)
            self._last_extraction_at = datetime.now(timezone.utc).isoformat()

            if self._feature_repository is not None:
                await self.process_durable_work(max_items=max(100, len(features)))

            if features:
                logger.info(
                    "Extracted %d feature vectors from %d windows",
                    len(features),
                    len(pending_windows),
                )

            return features

        except Exception as exc:
            self._extraction_errors += 1
            logger.error(
                "Failed to extract pending features exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )
            return []

    async def process_durable_work(self, *, max_items: int = 100) -> int:
        """Resume durable contribution and feature-window work.

        Both queues use the same PostgreSQL lease/retry state machine. A
        cancellation leaves claimed rows in ``processing`` until their lease
        expires; no incomplete work is marked complete during shutdown.
        """
        if self._feature_repository is None:
            return 0
        processed = 0
        limit = max(1, max_items)

        contribution_handler = {
            "feature_contribution": self._handle_feature_contribution,
        }
        feature_handler = {"feature_window": self._handle_feature_window}

        for queue, handlers in (
            (self._contribution_queue, contribution_handler),
            (self._feature_work_queue, feature_handler),
        ):
            if queue is None:
                continue
            for _ in range(limit):
                failures_before = queue.failures
                dead_before = queue.dead
                try:
                    did_work = await queue.process_one(handlers)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._feature_failures += 1
                    logger.error(
                        "Durable feature queue access failed exception_type=%s detail=%s",
                        type(exc).__name__,
                        sanitize_error_text(exc),
                    )
                    break
                if queue.failures > failures_before:
                    failure_delta = queue.failures - failures_before
                    self._feature_failures += failure_delta
                    self._feature_retries += failure_delta
                    FEATURE_FAILURES_TOTAL.inc(failure_delta)
                    FEATURE_RETRIES_TOTAL.inc(
                        max(0, failure_delta - (queue.dead - dead_before))
                    )
                    if queue is self._feature_work_queue:
                        self._last_feature_work_vector = None
                if not did_work:
                    break
                processed += 1
                if (
                    queue is self._feature_work_queue
                    and queue.failures == failures_before
                ):
                    vector = self._last_feature_work_vector
                    self._last_feature_work_vector = None
                    if vector is not None:
                        await self._emit_feature_telemetry(vector)
        return processed

    async def _handle_feature_contribution(
        self, row: dict[str, Any], conn: Any
    ) -> None:
        """Materialize one accepted source event before marking its work complete."""
        payload = row.get("payload")
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        parsed_log = ParsedLog.model_validate(payload)
        if str(row["tenant_id"]).strip() != str(parsed_log.tenant_id).strip():
            raise TenantBoundaryViolation(
                "tenant identity mismatch at feature-contribution"
            )
        repository = self._feature_repository
        if repository is None:
            raise RuntimeError("feature repository required for durable contribution")
        await repository.persist_feature_input_on_connection(conn, parsed_log)
        self.add_parsed_log(parsed_log)

    async def _handle_feature_window(self, row: dict[str, Any], conn: Any) -> None:
        """Persist one stable feature window and its critical anomaly path."""
        payload = row.get("payload")
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        feature_vector = FeatureVector.model_validate(payload)
        if str(row["tenant_id"]).strip() != str(feature_vector.tenant_id).strip():
            raise TenantBoundaryViolation("tenant identity mismatch at feature-window")
        self._last_feature_work_vector = feature_vector
        repository = self._feature_repository
        if repository is None:
            raise RuntimeError("feature repository required for durable feature window")
        await repository.persist_feature_vector_on_connection(
            conn, feature_vector.tenant_id, feature_vector
        )
        if self.event_manager is not None:
            process = getattr(self.event_manager, "process_feature_vector", None)
            if callable(process):
                await process(feature_vector, connection=conn, broadcast=False)

    async def _reconcile_recent_feature_inputs(self) -> None:
        """Rebuild only the active in-memory horizon from durable inputs."""
        if self._feature_repository is None:
            return
        horizon_seconds = max(
            self.window_config.window_size_seconds * 3,
            self.window_config.stride_seconds * 4,
            60,
        )
        since = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(
            seconds=horizon_seconds
        )
        try:
            rows = await self._feature_repository.get_recent_feature_inputs(
                since=since, limit=50000
            )
            prune = getattr(self._feature_repository, "prune_feature_inputs", None)
            if callable(prune):
                await prune(before=since)
        except Exception as exc:
            logger.error(
                "Failed to reconcile durable feature inputs exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )
            raise
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, str):
                import json

                payload = json.loads(payload)
            self.add_parsed_log(ParsedLog.model_validate(payload))

    async def _emit_feature_telemetry(self, feature_vector: FeatureVector) -> None:
        """Publish best-effort live hints only after durable work commits."""
        try:
            await telemetry_manager.broadcast(
                telemetry_event(
                    "feature.window.closed",
                    {
                        "window_id": feature_vector.window_id,
                        "window_start": _serialize_datetime(
                            feature_vector.window_start
                        ),
                        "window_end": _serialize_datetime(feature_vector.window_end),
                        "total_log_count": feature_vector.log_count,
                        "error_count": feature_vector.error_count,
                        "warning_count": feature_vector.warning_count,
                    },
                    tenant_id=feature_vector.tenant_id,
                    owner_user_id=feature_vector.owner_user_id,
                    logical_id=logical_telemetry_id(
                        "feature-window",
                        feature_vector.tenant_id,
                        feature_vector.window_id,
                        owner_user_id=feature_vector.owner_user_id,
                    ),
                )
            )
            prediction = feature_vector.anomaly_prediction
            if isinstance(prediction, dict) and prediction.get("is_anomaly") is True:
                await telemetry_manager.broadcast(
                    telemetry_event(
                        "anomaly.detected",
                        {
                            "window_id": feature_vector.window_id,
                            "anomaly_score": prediction.get("anomaly_score"),
                            "severity": prediction.get("severity"),
                            "model_version": prediction.get("model_version"),
                        },
                        tenant_id=feature_vector.tenant_id,
                        owner_user_id=feature_vector.owner_user_id,
                        logical_id=logical_telemetry_id(
                            "anomaly",
                            feature_vector.tenant_id,
                            feature_vector.window_id,
                            owner_user_id=feature_vector.owner_user_id,
                        ),
                    )
                )
        except Exception as exc:
            logger.error(
                "Feature telemetry hint failed after durable commit exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )

    def add_parsed_log(self, log: ParsedLog) -> None:
        """Add a parsed log to the feature extractor buffer.

        This is the main integration point with the Drain3 pipeline.
        """
        tenant_id = log.tenant_id or "default"
        scope_key = (tenant_id, int(log.owner_user_id))
        extractor = self._extractors.get(scope_key)
        if extractor is None:
            extractor = SlidingWindowExtractor(self.window_config)
            self._extractors[scope_key] = extractor
        extractor.add_log(log)

    def add_parsed_logs(self, logs: list[ParsedLog]) -> None:
        """Add multiple parsed logs to the buffer."""
        for log in logs:
            self.add_parsed_log(log)

    def get_recent_features(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent feature vectors as dicts, newest first."""
        safe_limit = max(0, limit)
        recent = list(self._feature_buffer)[-safe_limit:][::-1] if safe_limit else []
        return [fv.model_dump(mode="json") for fv in recent]

    def get_stats(self) -> dict[str, Any]:
        """Return worker statistics and extractor state."""
        return {
            "running": self._running,
            "extraction_interval_seconds": self.extraction_interval_seconds,
            "features_extracted": self._features_extracted,
            "extraction_errors": self._extraction_errors,
            "feature_retries": self._feature_retries,
            "feature_failures": self._feature_failures,
            # Durable failed-row counts are sampled from PostgreSQL by the
            # observability layer; this worker never aliases local errors to a
            # DLQ count.
            "feature_dlq_count": None,
            "last_extraction_at": self._last_extraction_at,
            "feature_buffer_size": len(self._feature_buffer),
            "model": self.get_model_health(),
            "extractor": self.extractor.get_stats(),
        }

    def get_model_health(self) -> dict[str, Any]:
        """Return bounded model lifecycle state for health/metrics adapters."""
        if self.anomaly_detector is not None:
            health = self.anomaly_detector.get_health(self.anomaly_model_path)
            if self.model_load_error:
                health["model_load_error"] = self.model_load_error
            return health

        return {
            "model_loaded": False,
            "model_version": None,
            "model_age_seconds": None,
            "artifact_path": str(self.anomaly_model_path)
            if self.anomaly_model_path
            else None,
            "inference_total": 0,
            "inference_errors_total": 0,
            "anomalies_total": 0,
            "model_load_error": self.model_load_error,
        }

    async def _detector_for_tenant(
        self, tenant_id: str, owner_user_id: int
    ) -> IsolationForestAnomalyDetector | None:
        """Load only the model belonging to the tenant being processed."""
        now = asyncio.get_running_loop().time()
        scope_key = (tenant_id, owner_user_id)
        if (
            scope_key in self._anomaly_detectors
            and now - self._model_checked_at.get(scope_key, 0) < 30
        ):
            return self._anomaly_detectors[scope_key]
        if self.anomaly_model_path is None:
            return None
        try:
            registered = await load_registered_detector(
                self.anomaly_model_path, tenant_id, owner_user_id
            )
            if registered is not None:
                version, registered_detector = registered
                if self._model_versions.get(scope_key) != version:
                    self._anomaly_detectors[scope_key] = registered_detector
                    self._model_versions[scope_key] = version
                    logger.info("Activated validated tenant model version=%s", version)
                self._model_checked_at[scope_key] = now
                return self._anomaly_detectors[scope_key]
            active_detector = load_active_detector(
                self.anomaly_model_path, tenant_id, owner_user_id
            )
        except Exception as exc:
            logger.error(
                "Failed to reload active tenant model; retaining known-good model exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )
            self._model_checked_at[scope_key] = now
            return self._anomaly_detectors.get(scope_key)
        if active_detector is None:
            return self._anomaly_detectors.get(scope_key)
        self._anomaly_detectors[scope_key] = active_detector
        self._model_checked_at[scope_key] = now
        return active_detector

    def clear_buffers(self) -> dict[str, int]:
        """Clear all internal buffers (for testing/debugging)."""
        logs_removed = sum(
            extractor.clear_buffer() for extractor in self._extractors.values()
        )
        features_removed = len(self._feature_buffer)
        self._feature_buffer.clear()

        return {
            "logs_removed": logs_removed,
            "features_removed": features_removed,
        }

    def _resolve_anomaly_detector(
        self,
        anomaly_detector: IsolationForestAnomalyDetector | None,
        anomaly_model_path: str | Path | None,
    ) -> IsolationForestAnomalyDetector | None:
        # Runtime inference is resolved through the tenant-scoped model
        # registry in _detector_for_tenant().  Loading the legacy canonical
        # artifact here would allow one tenant's model to score another
        # tenant's features and would bypass active/previous promotion state.
        if anomaly_detector is not None:
            return anomaly_detector
        return None

    async def _persist(self, feature_vector: FeatureVector) -> None:
        """Persist before acknowledging downstream work; failures propagate."""
        if self._feature_repository is None:
            return
        await self._feature_repository.persist_feature_vector(
            feature_vector.tenant_id, feature_vector
        )  # type: ignore


def _serialize_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value

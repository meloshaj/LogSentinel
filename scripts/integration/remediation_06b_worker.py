"""Run the current pipeline worker with disposable fault/report hooks.

The hooks wrap existing worker methods from the outside; they are enabled only
when the harness sets the corresponding environment variables.  The pipeline
implementation, database transactions, Valkey consumer group, and ACK paths
remain the production code under test.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _configured_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_marker(path: Path | None, phase: str, **details: Any) -> None:
    if path is None:
        return
    payload = {
        "phase": phase,
        "instance": os.getenv("WORKER_INSTANCE_ID", ""),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _append_debug(path: Path | None, phase: str, **details: Any) -> None:
    if path is None:
        return
    payload = {
        "phase": phase,
        "instance": os.getenv("WORKER_INSTANCE_ID", ""),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")


def _extractor_debug(application: Any) -> dict[str, Any]:
    return {
        str(scope): {
            "timestamps": [
                log.timestamp.isoformat()
                for log in getattr(extractor, "_log_buffer", [])
            ],
            "last_window_end": (
                extractor._last_window_end.isoformat()
                if extractor._last_window_end is not None
                else None
            ),
            "pending_cursor": (
                extractor._pending_cursor.isoformat()
                if extractor._pending_cursor is not None
                else None
            ),
        }
        for scope, extractor in application.feature_worker._extractors.items()
    }


async def _hold_until_released(path: Path | None) -> None:
    release = _configured_path("LS06B_FAULT_RELEASE")
    if path is None or release is None:
        return
    while not release.exists():
        await asyncio.sleep(0.05)


def _install_hooks(application: Any) -> None:
    marker = _configured_path("LS06B_FAULT_MARKER")
    phase = os.getenv("LS06B_FAULT_PHASE", "").strip()
    feature_debug = _configured_path("LS06B_FEATURE_DEBUG_PATH")

    if feature_debug is not None:
        original_reconcile = application.feature_worker._reconcile_recent_feature_inputs
        original_extract = application.feature_worker.extract_pending_features

        async def debug_reconcile() -> None:
            try:
                await original_reconcile()
            except Exception as exc:
                _append_debug(
                    feature_debug,
                    "reconcile_error",
                    error_type=type(exc).__name__,
                )
                raise
            _append_debug(
                feature_debug,
                "reconcile",
                scopes=len(application.feature_worker._extractors),
                buffer_size=sum(
                    len(getattr(extractor, "_log_buffer", []))
                    for extractor in application.feature_worker._extractors.values()
                ),
                extractor_state=_extractor_debug(application),
            )

        async def debug_extract(current_time: Any = None) -> Any:
            result = await original_extract(current_time=current_time)
            _append_debug(
                feature_debug,
                "extract",
                result_count=len(result),
                scopes=len(application.feature_worker._extractors),
                buffer_size=sum(
                    len(getattr(extractor, "_log_buffer", []))
                    for extractor in application.feature_worker._extractors.values()
                ),
                extractor_state=_extractor_debug(application),
            )
            return result

        application.feature_worker._reconcile_recent_feature_inputs = debug_reconcile
        application.feature_worker.extract_pending_features = debug_extract

    if phase == "before_db_commit":
        original_persist = application.batch_manager.persist_batch

        async def persist_before_commit(batch: list[Any]) -> Any:
            _write_marker(marker, phase, batch_size=len(batch))
            await _hold_until_released(marker)
            return await original_persist(batch)

        application.batch_manager.persist_batch = persist_before_commit

    if phase == "after_db_commit_before_xack":
        original_ack = application.drain_worker._ack_stream_message

        async def ack_after_commit(message_id: str) -> bool:
            _write_marker(marker, phase, message_id=message_id)
            await _hold_until_released(marker)
            return await original_ack(message_id)

        application.drain_worker._ack_stream_message = ack_after_commit

    target_window = os.getenv("LS06B_FAULT_FEATURE_WINDOW_ID", "").strip()
    if phase == "feature_partial_stage":
        original_feature_persist = (
            application.feature_repository.persist_feature_vector_on_connection
        )
        failed_once = False

        async def persist_feature_with_fault(
            connection: Any, tenant_id: str, feature_vector: Any
        ) -> None:
            nonlocal failed_once
            if not failed_once and (
                not target_window or feature_vector.window_id == target_window
            ):
                failed_once = True
                await original_feature_persist(connection, tenant_id, feature_vector)
                _write_marker(
                    marker,
                    phase,
                    window_id=feature_vector.window_id,
                    inserted_before_failure=True,
                )
                raise RuntimeError("remediation-06b partial feature-stage fault")
            await original_feature_persist(connection, tenant_id, feature_vector)

        application.feature_repository.persist_feature_vector_on_connection = (
            persist_feature_with_fault
        )

    report_key = os.getenv("LS06B_WORKER_REPORT_KEY", "").strip()
    if report_key:
        original_process = application.drain_worker._process_stream_message

        async def report_processed(message_id: str, entry: dict[Any, Any]) -> Any:
            result = await original_process(message_id, entry)
            redis_client = application.drain_worker.redis_client
            if redis_client is not None:
                await redis_client.incr(report_key)
            return result

        application.drain_worker._process_stream_message = report_processed


async def _run() -> None:
    import backend.app.main as application
    from backend.app.cli.worker import run

    _install_hooks(application)
    # The production recovery owner keeps a conservative 60-second idle
    # threshold.  This value only affects the disposable test's pending-entry
    # reclaim threshold; the polling and XAUTOCLAIM implementation stay intact.
    application.drain_worker.recovery_idle_time_ms = int(
        os.getenv("LS06B_RECOVERY_IDLE_MS", "1000")
    )
    await run("pipeline")


if __name__ == "__main__":
    asyncio.run(_run())

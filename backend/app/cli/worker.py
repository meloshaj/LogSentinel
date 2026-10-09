"""Explicit standalone worker entrypoint used by production orchestration."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
from datetime import datetime, timezone
from typing import Protocol

from ..core import (
    dispose_engine,
    get_database_settings,
    init_engine,
    verify_connectivity,
    verify_schema_ready,
)
from ..core.redis import close_redis_pool, init_redis_pool

logger = logging.getLogger("logsentinel.worker_runner")
HEARTBEAT_INTERVAL_SECONDS = 20
HEARTBEAT_TTL_SECONDS = 60


class WorkerLifecycle(Protocol):
    """Common lifecycle implemented by every standalone worker role."""

    def start(self) -> None: ...

    async def stop(self) -> None: ...


def _instance_id() -> str:
    return (
        os.getenv("WORKER_INSTANCE_ID", socket.gethostname()).strip()
        or socket.gethostname()
    )


def _heartbeat_key(role: str, instance: str) -> str:
    return f"logsentinel:worker-heartbeat:{role}:{instance}"


async def _heartbeat_loop(redis, role: str, instance: str, stop: asyncio.Event) -> None:
    key = _heartbeat_key(role, instance)
    while not stop.is_set():
        payload = json.dumps(
            {
                "role": role,
                "instance": instance,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        await redis.set(key, payload, ex=HEARTBEAT_TTL_SECONDS)
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except TimeoutError:
            pass


async def healthcheck(role: str) -> None:
    redis = await init_redis_pool()
    try:
        if not await redis.get(_heartbeat_key(role, _instance_id())):
            raise RuntimeError(f"no current heartbeat for {role}")
    finally:
        await close_redis_pool()


async def run(role: str) -> None:
    if os.getenv("ENVIRONMENT", "development").lower() == "production":
        if os.getenv("RUN_EMBEDDED_WORKERS", "").lower() != "false":
            raise RuntimeError(
                "standalone production workers require RUN_EMBEDDED_WORKERS=false"
            )
        if os.getenv("RUN_WEBHOOK_WORKER_IN_LIFESPAN", "").lower() != "false":
            raise RuntimeError(
                "standalone production workers require RUN_WEBHOOK_WORKER_IN_LIFESPAN=false"
            )
        if os.getenv("RUN_ARCHIVE_WORKER_IN_LIFESPAN", "").lower() != "false":
            raise RuntimeError(
                "standalone production workers require RUN_ARCHIVE_WORKER_IN_LIFESPAN=false"
            )

    from .. import main as application

    init_engine(get_database_settings())
    redis = await init_redis_pool()
    await verify_connectivity()
    await verify_schema_ready()
    await application.ensure_stream_and_group(
        redis, application.LOG_STREAM_NAME, application.LOG_WORKERS_GROUP
    )
    application.telemetry_manager.set_redis_client(redis)
    application.telemetry_manager.start()

    workers: list[WorkerLifecycle]
    if role == "pipeline":
        workers = [
            application.drain_worker,
            application.feature_worker,
            application.event_manager,
            application.stream_cleaner,
        ]
    elif role == "webhook":
        # One durable-delivery process owns both outboxes, avoiding another
        # production worker role while keeping their schemas and policies distinct.
        workers = [
            application.webhook_delivery_worker,
            application.email_delivery_worker,
        ]
    elif role == "archive":
        from ..archive.worker import ArchiveWorker

        workers = [ArchiveWorker(check_interval_seconds=60.0)]
    else:
        raise ValueError(f"unsupported production worker role: {role}")

    for worker in workers:
        if hasattr(worker, "set_redis_client"):
            worker.set_redis_client(redis)
        worker.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            # add_signal_handler is unavailable on the Windows event loop.
            pass
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(redis, role, _instance_id(), stop),
        name=f"{role}-heartbeat",
    )
    try:
        stop_task = asyncio.create_task(stop.wait(), name=f"{role}-shutdown")
        owned_tasks = [
            task
            for worker in workers
            if isinstance((task := getattr(worker, "_task", None)), asyncio.Task)
        ]
        done, pending = await asyncio.wait(
            [stop_task, heartbeat_task, *owned_tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task not in done:
            failed = next(iter(done))
            if failed.cancelled():
                raise RuntimeError(f"{role} owned task was cancelled unexpectedly")
            exception = failed.exception()
            if exception is not None:
                raise RuntimeError(f"{role} owned task failed") from exception
            raise RuntimeError(f"{role} owned task exited unexpectedly")
        for task in pending:
            if task is stop_task:
                task.cancel()
    finally:
        stop.set()
        if not heartbeat_task.done():
            await heartbeat_task
        # The composite pipeline must stop intake first, then flush feature
        # work before stopping event processing. Reversing this order can feed
        # already-stopped downstream in-memory stages.
        for worker in workers:
            await worker.stop()
        await application.telemetry_manager.stop()
        await dispose_engine()
        await close_redis_pool()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run or check a LogSentinel worker role"
    )
    parser.add_argument("command", choices=("run", "healthcheck"))
    parser.add_argument("role", choices=("pipeline", "webhook", "archive"))
    args = parser.parse_args()
    asyncio.run(run(args.role) if args.command == "run" else healthcheck(args.role))


if __name__ == "__main__":
    main()

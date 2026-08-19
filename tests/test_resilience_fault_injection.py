from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio

import backend.app.main as main_module
from backend.app.core.database import dispose_engine
from backend.app.core.settings import DatabaseSettings
from backend.app.ml.anomaly_detector import IsolationForestAnomalyDetector
from backend.app.ml.feature_extractor import WindowConfig
from backend.app.models import FeatureVector, ParsedLog
from backend.app.repositories.feature_repository import FeatureRepository
from backend.app.repositories.log_repository import LogRepository
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.services.drain_parser import DrainParser
from backend.app.services.runtime_dependency_parser import RuntimeDependencyParser
from backend.app.workers.drain_worker import DrainWorker
from backend.app.workers.feature_worker import FeatureExtractionWorker


pytestmark = pytest.mark.asyncio

INGEST_API_KEY = "resilience-ingest-key"




@dataclass
class PgConnectionSettings:
    user: str
    password: str
    host: str
    port: int
    database: str
    ssl_mode: str = "disable"

    def with_database(self, database: str) -> "PgConnectionSettings":
        return PgConnectionSettings(
            user=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=database,
            ssl_mode=self.ssl_mode,
        )

    def asyncpg_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "timeout": 5.0,
            "command_timeout": 30.0,
        }
        if self.ssl_mode == "disable":
            kwargs["ssl"] = False
        return kwargs

    def database_settings(self) -> DatabaseSettings:
        return DatabaseSettings(
            user=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            db_name=self.database,
            pool_size=5,
            max_overflow=5,
            ssl_mode=self.ssl_mode,
        )


class NoopEventManager:
    def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class ToggleablePersistenceSink:
    """Fault injector that behaves like a dead asyncpg connection when disabled."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.available = True
        self.failed_attempts = 0
        self.successful_attempts = 0

    async def __call__(self, batch: list[ParsedLog]) -> int:
        if not self.available:
            self.failed_attempts += 1
            raise asyncpg.ConnectionDoesNotExistError(
                "simulated database network partition"
            )
        self.successful_attempts += 1
        return await self.delegate(batch)


def _postgres_settings_from_env() -> PgConnectionSettings:
    raw_url = (
        os.getenv("LOGSENTINEL_TEST_DATABASE_URL")
        or os.getenv("TEST_DATABASE_URL")
        or os.getenv("DATABASE_URL")
    )
    if raw_url:
        parsed = urlparse(raw_url.replace("postgresql+asyncpg://", "postgresql://", 1))
        query = parse_qs(parsed.query)
        return PgConnectionSettings(
            user=unquote(parsed.username or "logsentinel"),
            password=unquote(parsed.password or "logsentinel_secret"),
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 5432,
            database=(parsed.path or "/postgres").lstrip("/") or "postgres",
            ssl_mode=query.get("ssl", ["disable"])[0],
        )

    return PgConnectionSettings(
        user=os.getenv("POSTGRES_USER", "logsentinel"),
        password=os.getenv("POSTGRES_PASSWORD", "logsentinel_secret"),
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "logsentinel_db"),
        ssl_mode=os.getenv("POSTGRES_SSL_MODE", "disable"),
    )


def _quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", identifier):
        raise ValueError(f"unsafe PostgreSQL identifier: {identifier!r}")
    return f'"{identifier}"'


async def _create_temporary_database() -> PgConnectionSettings:
    base = _postgres_settings_from_env()
    admin = base.with_database(os.getenv("LOGSENTINEL_TEST_MAINTENANCE_DB", "postgres"))
    database_name = f"logsentinel_res_{uuid4().hex[:16]}"

    try:
        connection = await asyncpg.connect(**admin.asyncpg_kwargs())
    except OSError as exc:
        pytest.skip(f"PostgreSQL resilience database is not reachable: {exc}")
    except asyncpg.PostgresError as exc:
        pytest.skip(f"Cannot connect to PostgreSQL maintenance database: {exc}")

    try:
        await connection.execute(f"CREATE DATABASE {_quote_identifier(database_name)}")
    except asyncpg.InsufficientPrivilegeError as exc:
        pytest.skip(f"PostgreSQL user cannot create isolated test databases: {exc}")
    finally:
        await connection.close()

    return base.with_database(database_name)


async def _drop_temporary_database(settings: PgConnectionSettings) -> None:
    base = _postgres_settings_from_env()
    admin = base.with_database(os.getenv("LOGSENTINEL_TEST_MAINTENANCE_DB", "postgres"))
    connection = await asyncpg.connect(**admin.asyncpg_kwargs())
    try:
        await connection.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            settings.database,
        )
        await connection.execute(f"DROP DATABASE IF EXISTS {_quote_identifier(settings.database)}")
    finally:
        await connection.close()


def _training_feature_vector(index: int) -> FeatureVector:
    now = datetime.now(timezone.utc)
    log_count = 500 + (index % 25)
    features = {
        "log_count": float(log_count),
        "info_count": float(log_count),
        "warning_count": 0.0,
        "error_count": 0.0,
        "error_ratio": 0.0,
        "active_services": 4.0,
        "unique_templates": float(4 + (index % 4)),
        "dominant_service_count": float(180 + (index % 20)),
        "dominant_template_count": float(225 + (index % 20)),
        "logs_per_second": float(log_count / 60.0),
        "avg_logs_per_minute": float(log_count),
        "burst_indicator": 1.0,
    }
    return FeatureVector(
        window_id=f"resilience-training-{index}",
        timestamp=now,
        window_start=now - timedelta(seconds=60),
        window_end=now,
        log_count=log_count,
        unique_templates=int(features["unique_templates"]),
        error_count=0,
        warning_count=0,
        template_frequencies={"template-normal": 1.0},
        template_entropy=0.0,
        service_distribution={"orders": 200, "payments": 125, "inventory": 100, "api": 75},
        logs_per_second=features["logs_per_second"],
        feature_array=[float(value) for value in features.values()],
        feature_names=list(features.keys()),
        features=features,
    )


def _trained_detector() -> IsolationForestAnomalyDetector:
    detector = IsolationForestAnomalyDetector(random_state=11, contamination=0.05)
    detector.train([_training_feature_vector(index) for index in range(50)])
    return detector


def _install_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    batch_size: int,
    state_path: Path,
) -> SimpleNamespace:
    from drain3.file_persistence import FilePersistence
    state_path_str = str(state_path)
    pers = FilePersistence(state_path_str)
    drain_parser = DrainParser(state_path=state_path_str, persistence=pers)
    log_repository = LogRepository()
    feature_repository = FeatureRepository()
    batch_manager = ParsedLogBatchManager(
        batch_size=batch_size,
        flush_interval_seconds=3600.0,
        sink=log_repository.bulk_insert_parsed_logs,
    )
    feature_worker = FeatureExtractionWorker(
        window_config=WindowConfig(
            window_size_seconds=60,
            stride_seconds=60,
            min_logs_per_window=1,
        ),
        extraction_interval_seconds=3600.0,
        anomaly_detector=_trained_detector(),
        feature_repository=feature_repository,
        event_manager=None,
    )
    drain_worker = DrainWorker(
        None,
        drain_parser,
        batch_manager=batch_manager,
        on_log_parsed=feature_worker.add_parsed_log,
        runtime_dependency_parser=RuntimeDependencyParser(),
        queue_drain_timeout_seconds=120.0,
    )


    monkeypatch.setattr(main_module, "drain_parser", drain_parser)
    monkeypatch.setattr(main_module, "log_repository", log_repository)
    monkeypatch.setattr(main_module, "feature_repository", feature_repository)
    monkeypatch.setattr(main_module, "batch_manager", batch_manager)
    monkeypatch.setattr(main_module, "feature_worker", feature_worker)
    monkeypatch.setattr(main_module, "drain_worker", drain_worker)
    monkeypatch.setattr(main_module, "event_manager", NoopEventManager())

    return SimpleNamespace(
        drain_parser=drain_parser,
        log_repository=log_repository,
        feature_repository=feature_repository,
        batch_manager=batch_manager,
        feature_worker=feature_worker,
        drain_worker=drain_worker,
    )


@pytest_asyncio.fixture
async def resilience_pipeline(monkeypatch: pytest.MonkeyPatch):
    await dispose_engine()
    db_settings = await _create_temporary_database()
    state_dir = Path(__file__).resolve().parent / ".resilience_state"
    state_dir.mkdir(exist_ok=True)
    state_path = state_dir / f"drain3_state_{uuid4().hex}.bin"
    pool: asyncpg.Pool | None = None

    try:
        pool = await asyncpg.create_pool(
            **db_settings.asyncpg_kwargs(),
            min_size=1,
            max_size=5,
        )
        pipeline = _install_pipeline(
            monkeypatch,
            batch_size=10_000,
            state_path=state_path,
        )
        monkeypatch.setattr(
            main_module,
            "get_database_settings",
            lambda: db_settings.database_settings(),
        )
        monkeypatch.setenv("INGEST_API_KEY", INGEST_API_KEY)
        monkeypatch.setenv("INGEST_API_KEYS", "")

        transport = httpx.ASGITransport(app=main_module.app)
        async with main_module.lifespan(main_module.app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                timeout=httpx.Timeout(30.0),
            ) as client:
                yield SimpleNamespace(
                    client=client,
                    pool=pool,
                    **pipeline.__dict__,
                )
    finally:
        if pool is not None:
            await pool.close()
        await dispose_engine()
        await _drop_temporary_database(db_settings)
        state_path.unlink(missing_ok=True)
        try:
            state_dir.rmdir()
        except OSError:
            pass


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": INGEST_API_KEY}


def _log_payload(run_id: str, index: int) -> dict[str, object]:
    service = ("api", "orders", "payments", "inventory")[index % 4]
    return {
        "source": "resilience-test",
        "environment": "test",
        "correlation_id": f"{run_id}-{index}",
        "logs": [
            {
                "service_name": service,
                "level": "info",
                "message": (
                    f"{service} request {run_id}-{index} completed "
                    f"in {10 + index % 17}ms"
                ),
                "metadata": {
                    "request_id": f"{run_id}-request-{index}",
                    "trace_id": f"{run_id}-trace-{index // 8}",
                },
            }
        ],
    }


async def _wait_for_fetchval(
    pool: asyncpg.Pool,
    query: str,
    *args,
    expected,
    timeout: float = 15.0,
):
    deadline = time.monotonic() + timeout
    last_value = None
    while time.monotonic() < deadline:
        last_value = await pool.fetchval(query, *args)
        if expected(last_value):
            return last_value
        await asyncio.sleep(0.05)
    pytest.fail(f"Timed out waiting for database condition; last value={last_value!r}")


async def _drain_and_flush(pipeline: SimpleNamespace, *, timeout: float = 120.0) -> None:
    await asyncio.sleep(0.5)
    assert await pipeline.batch_manager.flush() is True


async def test_queue_backpressure_saturation(monkeypatch: pytest.MonkeyPatch) -> None:
    class MockRedisPipeline:
        def xadd(self, *args, **kwargs):
            pass
        def xlen(self, *args, **kwargs):
            pass
        async def execute(self):
            raise Exception("Simulated Redis failure")

    class MockRedis:
        def pipeline(self, transaction=False):
            return MockRedisPipeline()

    monkeypatch.setattr(main_module.app.state, "redis", MockRedis(), raising=False)
    monkeypatch.setenv("INGEST_API_KEY", INGEST_API_KEY)
    monkeypatch.setenv("INGEST_API_KEYS", "")

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=httpx.Timeout(5.0),
    ) as client:
        response = await client.post(
            "/ingest-log",
            json=_log_payload("saturated", 0),
            headers=_auth_headers(),
        )

    assert response.status_code in {429, 503}
    assert response.json()["accepted"] is False


async def test_database_disconnection_resilience(resilience_pipeline) -> None:
    run_id = f"db-fault-{uuid4().hex}"
    sink = ToggleablePersistenceSink(
        resilience_pipeline.log_repository.bulk_insert_parsed_logs
    )
    resilience_pipeline.batch_manager.sink = sink
    sink.available = False

    responses = await asyncio.gather(
        *[
            resilience_pipeline.client.post(
                "/ingest-log",
                json=_log_payload(run_id, index),
                headers=_auth_headers(),
            )
            for index in range(50)
        ]
    )

    assert all(response.status_code == 202 for response in responses)
    await asyncio.sleep(0.5)

    flush_result = await resilience_pipeline.batch_manager.flush()
    stats_after_failure = resilience_pipeline.batch_manager.get_stats()

    assert flush_result is False
    assert sink.failed_attempts == 1
    assert stats_after_failure["current_buffer_size"] == 50
    assert stats_after_failure["failed_flush_attempt_count"] == 1
    assert await resilience_pipeline.pool.fetchval(
        "SELECT count(*) FROM logs WHERE correlation_id LIKE $1",
        f"{run_id}-%",
    ) == 0

    sink.available = True
    assert await resilience_pipeline.batch_manager.flush() is True
    persisted = await _wait_for_fetchval(
        resilience_pipeline.pool,
        "SELECT count(*) FROM logs WHERE correlation_id LIKE $1",
        f"{run_id}-%",
        expected=lambda value: value == 50,
        timeout=15.0,
    )

    assert persisted == 50
    assert sink.successful_attempts == 1
    assert resilience_pipeline.batch_manager.get_stats()["current_buffer_size"] == 0
    assert resilience_pipeline.drain_worker.get_stats()["error_count"] == 0


async def test_high_concurrency_burst(resilience_pipeline) -> None:
    request_count = 5_000
    run_id = f"burst-{uuid4().hex}"

    async def send_one(index: int):
        return await resilience_pipeline.client.post(
            "/ingest-log",
            json=_log_payload(run_id, index),
            headers=_auth_headers(),
        )

    results = await asyncio.gather(
        *(send_one(index) for index in range(request_count)),
        return_exceptions=True,
    )

    exceptions = [result for result in results if isinstance(result, Exception)]
    responses = [result for result in results if isinstance(result, httpx.Response)]

    assert exceptions == []
    assert len(responses) == request_count
    assert all(response.status_code == 202 for response in responses)
    assert all(response.json()["accepted"] is True for response in responses)

    await _drain_and_flush(resilience_pipeline, timeout=180.0)
    persisted = await _wait_for_fetchval(
        resilience_pipeline.pool,
        "SELECT count(*) FROM logs WHERE correlation_id LIKE $1",
        f"{run_id}-%",
        expected=lambda value: value == request_count,
        timeout=30.0,
    )

    worker_stats = resilience_pipeline.drain_worker.get_stats()
    batch_stats = resilience_pipeline.batch_manager.get_stats()

    assert persisted == request_count
    assert worker_stats["processed_count"] >= request_count
    assert worker_stats["error_count"] == 0
    assert batch_stats["current_buffer_size"] == 0
    assert batch_stats["failed_flush_attempt_count"] == 0
    assert resilience_pipeline.drain_worker._task is not None
    assert not resilience_pipeline.drain_worker._task.done()

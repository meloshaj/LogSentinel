from __future__ import annotations

import asyncio
import json
import logging
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
from backend.app.models import FeatureVector
from backend.app.repositories.feature_repository import FeatureRepository
from backend.app.repositories.log_repository import LogRepository
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.services.drain_parser import DrainParser
from backend.app.services.runtime_dependency_parser import RuntimeDependencyParser
from backend.app.workers.drain_worker import DrainWorker
from backend.app.workers.feature_worker import FeatureExtractionWorker


pytestmark = pytest.mark.asyncio

INGEST_API_KEY = "integration-ingest-key"


@dataclass(frozen=True)
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
            pool_size=3,
            max_overflow=2,
            ssl_mode=self.ssl_mode,
        )


class NoopEventManager:
    def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


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
    maintenance_db = os.getenv("LOGSENTINEL_TEST_MAINTENANCE_DB", "postgres")
    admin = base.with_database(maintenance_db)
    test_db_name = f"logsentinel_it_{uuid4().hex[:16]}"

    try:
        connection = await asyncpg.connect(**admin.asyncpg_kwargs())
    except OSError as exc:
        pytest.skip(f"PostgreSQL integration database is not reachable: {exc}")
    except asyncpg.PostgresError as exc:
        pytest.skip(f"Cannot connect to PostgreSQL maintenance database: {exc}")

    try:
        await connection.execute(f"CREATE DATABASE {_quote_identifier(test_db_name)}")
    except asyncpg.InsufficientPrivilegeError as exc:
        pytest.skip(f"PostgreSQL user cannot create isolated test databases: {exc}")
    finally:
        await connection.close()

    return base.with_database(test_db_name)


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
    log_count = 90 + (index % 7)
    features = {
        "log_count": float(log_count),
        "info_count": float(log_count),
        "warning_count": 0.0,
        "error_count": 0.0,
        "error_ratio": 0.0,
        "active_services": 3.0,
        "unique_templates": float(3 + (index % 3)),
        "dominant_service_count": float(35 + (index % 5)),
        "dominant_template_count": float(40 + (index % 6)),
        "logs_per_second": float(log_count / 60.0),
        "avg_logs_per_minute": float(log_count),
        "burst_indicator": 0.0,
    }
    return FeatureVector(
        window_id=f"training-{index}",
        timestamp=now,
        window_start=now - timedelta(seconds=60),
        window_end=now,
        log_count=log_count,
        unique_templates=int(features["unique_templates"]),
        error_count=0,
        warning_count=0,
        template_frequencies={"template-normal": 1.0},
        template_entropy=0.0,
        service_distribution={"orders": 40, "payments": 30, "inventory": 20},
        logs_per_second=features["logs_per_second"],
        feature_array=[float(value) for value in features.values()],
        feature_names=list(features.keys()),
        features=features,
    )


def _trained_detector() -> IsolationForestAnomalyDetector:
    detector = IsolationForestAnomalyDetector(random_state=7, contamination=0.05)
    detector.train([_training_feature_vector(index) for index in range(40)])
    return detector


def _install_isolated_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    state_path: Path,
) -> SimpleNamespace:
    log_buffer = main_module.AsyncLogBuffer(maxsize=1000)
    drain_parser = DrainParser(state_path=str(state_path))
    log_repository = LogRepository()
    feature_repository = FeatureRepository()
    batch_manager = ParsedLogBatchManager(
        batch_size=500,
        flush_interval_seconds=60.0,
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
        log_buffer,
        drain_parser,
        batch_manager=batch_manager,
        on_log_parsed=feature_worker.add_parsed_log,
        runtime_dependency_parser=RuntimeDependencyParser(),
        queue_drain_timeout_seconds=10.0,
    )

    monkeypatch.setattr(main_module, "log_buffer", log_buffer)
    monkeypatch.setattr(main_module, "drain_parser", drain_parser)
    monkeypatch.setattr(main_module, "log_repository", log_repository)
    monkeypatch.setattr(main_module, "feature_repository", feature_repository)
    monkeypatch.setattr(main_module, "batch_manager", batch_manager)
    monkeypatch.setattr(main_module, "feature_worker", feature_worker)
    monkeypatch.setattr(main_module, "drain_worker", drain_worker)
    monkeypatch.setattr(main_module, "event_manager", NoopEventManager())

    return SimpleNamespace(
        log_buffer=log_buffer,
        drain_parser=drain_parser,
        log_repository=log_repository,
        feature_repository=feature_repository,
        batch_manager=batch_manager,
        feature_worker=feature_worker,
        drain_worker=drain_worker,
    )


@pytest_asyncio.fixture
async def integration_pipeline(monkeypatch: pytest.MonkeyPatch):
    await dispose_engine()
    db_settings = await _create_temporary_database()
    state_dir = Path(__file__).resolve().parent / ".integration_state"
    state_dir.mkdir(exist_ok=True)
    state_path = state_dir / f"drain3_state_{uuid4().hex}.bin"
    pool: asyncpg.Pool | None = None
    try:
        pool = await asyncpg.create_pool(
            **db_settings.asyncpg_kwargs(),
            min_size=1,
            max_size=3,
        )
        pipeline = _install_isolated_pipeline(monkeypatch, state_path)

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
                timeout=httpx.Timeout(5.0),
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


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": INGEST_API_KEY}


def _normal_event(index: int, run_id: str) -> dict[str, object]:
    service = ("orders", "payments", "inventory")[index % 3]
    return {
        "service_name": service,
        "level": "info",
        "message": f"{service} request {10_000 + index} completed in {20 + index % 5}ms",
        "metadata": {
            "request_id": f"{run_id}-request-{index}",
            "trace_id": f"{run_id}-trace-{index // 3}",
        },
    }


async def _wait_for_fetchval(pool: asyncpg.Pool, query: str, *args, expected, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    last_value = None
    while time.monotonic() < deadline:
        last_value = await pool.fetchval(query, *args)
        if expected(last_value):
            return last_value
        await asyncio.sleep(0.05)
    pytest.fail(f"Timed out waiting for database condition; last value={last_value!r}")


async def _drain_and_flush(pipeline: SimpleNamespace) -> None:
    await asyncio.wait_for(pipeline.log_buffer.join(), timeout=10.0)
    await pipeline.batch_manager.flush()


async def _extract_and_wait_for_features(pipeline: SimpleNamespace) -> list[FeatureVector]:
    features = await pipeline.feature_worker.extract_pending_features(
        current_time=datetime.now(timezone.utc) + timedelta(seconds=120),
    )
    assert features, "expected at least one closed feature window"
    await _wait_for_fetchval(
        pipeline.pool,
        "SELECT count(*) FROM feature_windows",
        expected=lambda value: int(value or 0) >= len(features),
        timeout=5.0,
    )
    return features


def _jsonb(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


async def test_e2e_valid_log_ingestion_to_db(integration_pipeline) -> None:
    run_id = f"valid-{uuid4().hex}"
    response_times: list[float] = []

    for index in range(100):
        payload = {
            "source": "integration-test",
            "environment": "test",
            "correlation_id": f"{run_id}-{index}",
            "logs": [_normal_event(index, run_id)],
        }
        started = time.perf_counter()
        response = await integration_pipeline.client.post(
            "/ingest-log",
            json=payload,
            headers=_auth_headers(),
        )
        response_times.append(time.perf_counter() - started)

        assert response.status_code == 202
        assert response.json()["accepted"] is True

    assert max(response_times) < 1.0

    await _drain_and_flush(integration_pipeline)
    persisted_count = await _wait_for_fetchval(
        integration_pipeline.pool,
        "SELECT count(*) FROM logs WHERE correlation_id LIKE $1",
        f"{run_id}-%",
        expected=lambda value: value == 100,
        timeout=5.0,
    )
    assert persisted_count == 100

    persisted_summary = await integration_pipeline.pool.fetchrow(
        """
        SELECT
            count(*) AS total_count,
            count(template_id) AS template_id_count,
            count(template_text) AS template_text_count,
            count(parsed_at) AS parsed_at_count
        FROM logs
        WHERE correlation_id LIKE $1
        """,
        f"{run_id}-%",
    )
    assert dict(persisted_summary) == {
        "total_count": 100,
        "template_id_count": 100,
        "template_text_count": 100,
        "parsed_at_count": 100,
    }

    features = await _extract_and_wait_for_features(integration_pipeline)
    feature_rows = await integration_pipeline.pool.fetch(
        """
        SELECT window_id, log_count, feature_vector, anomaly_prediction
        FROM feature_windows
        ORDER BY created_at ASC
        """
    )
    assert len(feature_rows) >= len(features)
    assert sum(row["log_count"] for row in feature_rows) >= 100

    for row in feature_rows:
        feature_vector = _jsonb(row["feature_vector"])
        prediction = _jsonb(row["anomaly_prediction"])
        assert row["window_id"]
        assert feature_vector["log_count"] > 0
        assert prediction["model_version"] == "isolation_forest_v1"
        assert prediction["raw_score"] is not None
        assert prediction["anomaly_score"] is not None

    worker_stats = integration_pipeline.drain_worker.get_stats()
    assert worker_stats["processed_count"] == 100
    assert worker_stats["queue_size"] == 0
    assert worker_stats["batch"]["last_sink_error"] is None


async def test_e2e_malformed_log_resilience(
    integration_pipeline,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = f"malformed-{uuid4().hex}"
    original_parse = integration_pipeline.drain_parser.parse

    def parse_with_one_failure(raw_message: str, metadata: dict | None = None):
        if "force parser exception" in raw_message:
            raise ValueError("forced parser validation failure")
        return original_parse(raw_message, metadata=metadata)

    monkeypatch.setattr(integration_pipeline.drain_parser, "parse", parse_with_one_failure)

    caplog.set_level(logging.WARNING, logger="logsentinel.drain_worker")

    corrupted_json = await integration_pipeline.client.post(
        "/ingest-log",
        content=b'{"source": "integration-test", "logs": [',
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )
    missing_fields = await integration_pipeline.client.post(
        "/ingest-log",
        json={"source": "integration-test", "environment": "test", "logs": [{"level": "info"}]},
        headers=_auth_headers(),
    )
    binary_blob = await integration_pipeline.client.post(
        "/ingest-log",
        content=b"\x80\x81\x82not-json",
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )
    massive_text = await integration_pipeline.client.post(
        "/ingest-log",
        json={
            "source": "integration-test",
            "environment": "test",
            "correlation_id": f"{run_id}-massive",
            "logs": [
                {
                    "service_name": "orders",
                    "level": "warning",
                    "message": "A" * 131_072,
                }
            ],
        },
        headers=_auth_headers(),
    )
    parser_failure = await integration_pipeline.client.post(
        "/ingest-log",
        json={
            "source": "integration-test",
            "environment": "test",
            "correlation_id": f"{run_id}-parser-failure",
            "logs": [
                {
                    "service_name": "orders",
                    "level": "error",
                    "message": "force parser exception",
                }
            ],
        },
        headers=_auth_headers(),
    )
    subsequent_valid = await integration_pipeline.client.post(
        "/ingest-log",
        json={
            "source": "integration-test",
            "environment": "test",
            "correlation_id": f"{run_id}-valid",
            "logs": [_normal_event(index, run_id) for index in range(5)],
        },
        headers=_auth_headers(),
    )

    assert corrupted_json.status_code == 422
    assert missing_fields.status_code == 422
    assert binary_blob.status_code in {400, 422}
    assert massive_text.status_code == 202
    assert parser_failure.status_code == 202
    assert subsequent_valid.status_code == 202

    await _drain_and_flush(integration_pipeline)

    valid_count = await _wait_for_fetchval(
        integration_pipeline.pool,
        "SELECT count(*) FROM logs WHERE correlation_id = $1",
        f"{run_id}-valid",
        expected=lambda value: value == 5,
        timeout=5.0,
    )
    massive_count = await integration_pipeline.pool.fetchval(
        "SELECT count(*) FROM logs WHERE correlation_id = $1",
        f"{run_id}-massive",
    )
    parser_failure_count = await integration_pipeline.pool.fetchval(
        "SELECT count(*) FROM logs WHERE correlation_id = $1",
        f"{run_id}-parser-failure",
    )

    assert valid_count == 5
    assert massive_count == 1
    assert parser_failure_count == 0
    assert integration_pipeline.log_buffer.queue_size() == 0
    assert integration_pipeline.drain_worker.get_stats()["error_count"] >= 1
    assert "Drain parser failed for log message" in caplog.text

    features = await _extract_and_wait_for_features(integration_pipeline)
    assert sum(feature.log_count for feature in features) >= 6

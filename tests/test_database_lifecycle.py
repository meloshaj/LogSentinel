"""Tests for the database lifecycle infrastructure.

Validates:
- ``DatabaseSettings`` construction and validation
- ``init_engine`` / ``get_engine`` / ``dispose_engine`` lifecycle
- ``get_async_session`` dependency yields usable sessions
- ``check_pool_health`` introspection
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from backend.app.core.database import (
    check_pool_health,
    dispose_engine,
    get_engine,
    get_session_factory,
    init_engine,
)
from backend.app.core.settings import (
    DatabaseSettings,
    Drain3PipelineSettings,
    GraphScoringSettings,
    get_database_settings,
    get_drain3_pipeline_settings,
    get_graph_scoring_settings,
)


# ---------------------------------------------------------------------------
# Helpers — reset module-level state between tests
# ---------------------------------------------------------------------------


def _reset_engine_state() -> None:
    """Force-clear the module-level engine state for test isolation."""
    import backend.app.core.database as db_mod

    db_mod._engine = None
    db_mod._session_factory = None


# ---------------------------------------------------------------------------
# DatabaseSettings tests
# ---------------------------------------------------------------------------


class TestDatabaseSettings:
    """Validate the Pydantic settings model."""

    def test_defaults(self) -> None:
        settings = DatabaseSettings()
        assert settings.user == "logsentinel"
        assert settings.password == ""
        assert settings.host == "127.0.0.1"
        assert settings.port == 5432
        assert settings.db_name == "logsentinel_db"
        assert settings.pool_size == 20
        assert settings.max_overflow == 10
        assert settings.pool_recycle_seconds == 1800
        assert settings.echo_sql is False
        assert settings.database_url_override is None

    def test_url_property_builds_dsn(self) -> None:
        settings = DatabaseSettings(
            user="u",
            password="p",
            host="dbhost",
            port=5433,
            db_name="mydb",
        )
        assert settings.url == "postgresql+asyncpg://u:p@dbhost:5433/mydb?ssl=disable"

    def test_url_override_takes_precedence(self) -> None:
        settings = DatabaseSettings(
            database_url_override="postgresql+asyncpg://override:pw@h:1/d",
        )
        assert settings.url == "postgresql+asyncpg://override:pw@h:1/d"

    def test_port_coerced_from_string(self) -> None:
        settings = DatabaseSettings(port="9999")  # type: ignore[arg-type]
        assert settings.port == 9999

    def test_invalid_port_rejected(self) -> None:
        with pytest.raises(Exception):
            DatabaseSettings(port=0)

    def test_invalid_pool_size_rejected(self) -> None:
        with pytest.raises(Exception):
            DatabaseSettings(pool_size=0)


class TestGetDatabaseSettings:
    """Validate the factory function reads environment variables."""

    def test_reads_env_vars(self) -> None:
        env = {
            "POSTGRES_USER": "testuser",
            "POSTGRES_PASSWORD": "testpass",
            "POSTGRES_HOST": "testhost",
            "POSTGRES_PORT": "6543",
            "POSTGRES_DB": "testdb",
            "POOL_SIZE": "5",
            "POOL_MAX_OVERFLOW": "2",
            "POOL_RECYCLE_SECONDS": "1800",
            "SQL_ECHO": "true",
        }
        with patch.dict("os.environ", env, clear=False):
            settings = get_database_settings()

        assert settings.user == "testuser"
        assert settings.password == "testpass"
        assert settings.host == "testhost"
        assert settings.port == 6543
        assert settings.db_name == "testdb"
        assert settings.pool_size == 5
        assert settings.max_overflow == 2
        assert settings.pool_recycle_seconds == 1800
        assert settings.echo_sql is True

    def test_database_url_override(self) -> None:
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql+asyncpg://x:y@z:1/d"}, clear=False):
            settings = get_database_settings()

        assert settings.url == "postgresql+asyncpg://x:y@z:1/d"


class TestDrain3PipelineSettings:
    """Validate centralized Drain3 batching and shutdown configuration."""

    def test_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = get_drain3_pipeline_settings()

        assert settings.batch_size == 500
        assert settings.flush_interval_seconds == 5.0
        assert settings.queue_drain_timeout_seconds == 30.0

    def test_reads_numeric_environment_overrides(self) -> None:
        env = {
            "DRAIN3_BATCH_SIZE": "125",
            "DRAIN3_FLUSH_INTERVAL_SECONDS": "2.5",
            "DRAIN3_QUEUE_DRAIN_TIMEOUT_SECONDS": "12.75",
        }
        with patch.dict("os.environ", env, clear=True):
            settings = get_drain3_pipeline_settings()

        assert settings.batch_size == 125
        assert settings.flush_interval_seconds == 2.5
        assert settings.queue_drain_timeout_seconds == 12.75

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("batch_size", 0),
            ("batch_size", -1),
            ("flush_interval_seconds", 0),
            ("flush_interval_seconds", -1.0),
            ("queue_drain_timeout_seconds", 0),
            ("queue_drain_timeout_seconds", -1.0),
        ],
    )
    def test_rejects_non_positive_values(self, field_name: str, value: float) -> None:
        with pytest.raises(ValueError):
            Drain3PipelineSettings(**{field_name: value})

    @pytest.mark.parametrize(
        ("environment_name", "value"),
        [
            ("DRAIN3_BATCH_SIZE", "0"),
            ("DRAIN3_BATCH_SIZE", "-1"),
            ("DRAIN3_FLUSH_INTERVAL_SECONDS", "0"),
            ("DRAIN3_FLUSH_INTERVAL_SECONDS", "-1"),
            ("DRAIN3_QUEUE_DRAIN_TIMEOUT_SECONDS", "0"),
            ("DRAIN3_QUEUE_DRAIN_TIMEOUT_SECONDS", "-1"),
        ],
    )
    def test_rejects_non_positive_environment_values(
        self,
        environment_name: str,
        value: str,
    ) -> None:
        with patch.dict("os.environ", {environment_name: value}, clear=True):
            with pytest.raises(ValueError):
                get_drain3_pipeline_settings()

    @pytest.mark.parametrize(
        ("environment_name", "value"),
        [
            ("DRAIN3_BATCH_SIZE", "not-an-integer"),
            ("DRAIN3_FLUSH_INTERVAL_SECONDS", "not-a-number"),
            ("DRAIN3_QUEUE_DRAIN_TIMEOUT_SECONDS", "not-a-number"),
        ],
    )
    def test_rejects_invalid_numeric_environment_values(
        self,
        environment_name: str,
        value: str,
    ) -> None:
        with patch.dict("os.environ", {environment_name: value}, clear=True):
            with pytest.raises(ValueError):
                get_drain3_pipeline_settings()


class TestGraphScoringSettings:
    """Validate graph-scoring runtime settings."""

    def test_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = get_graph_scoring_settings()

        assert settings.enabled is True
        assert settings.lookback_seconds == 180
        assert settings.timeout_seconds == 2.0
        assert settings.max_anomaly_events == 500
        assert settings.max_log_records == 5000

    def test_reads_environment_overrides(self) -> None:
        env = {
            "GRAPH_SCORING_ENABLED": "false",
            "GRAPH_SCORING_LOOKBACK_SECONDS": "240",
            "GRAPH_SCORING_TIMEOUT_SECONDS": "1.5",
            "GRAPH_SCORING_MAX_ANOMALY_EVENTS": "50",
            "GRAPH_SCORING_MAX_LOG_RECORDS": "100",
        }
        with patch.dict("os.environ", env, clear=True):
            settings = get_graph_scoring_settings()

        assert settings.enabled is False
        assert settings.lookback_seconds == 240
        assert settings.timeout_seconds == 1.5
        assert settings.max_anomaly_events == 50
        assert settings.max_log_records == 100

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("lookback_seconds", 0),
            ("timeout_seconds", 0.0),
            ("max_anomaly_events", 0),
            ("max_log_records", 0),
        ],
    )
    def test_rejects_non_positive_values(self, field_name: str, value: float) -> None:
        with pytest.raises(ValueError):
            GraphScoringSettings(**{field_name: value})


# ---------------------------------------------------------------------------
# Engine lifecycle tests
# ---------------------------------------------------------------------------


class TestEngineLifecycle:
    """Validate init_engine / get_engine / dispose_engine."""

    def setup_method(self) -> None:
        _reset_engine_state()

    def teardown_method(self) -> None:
        _reset_engine_state()

    def test_get_engine_raises_before_init(self) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            get_engine()

    def test_get_session_factory_raises_before_init(self) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            get_session_factory()

    def test_init_engine_creates_engine(self) -> None:
        settings = DatabaseSettings()
        engine = init_engine(settings)

        assert engine is not None
        assert get_engine() is engine
        assert get_session_factory() is not None

        # Clean up
        asyncio.run(dispose_engine())

    def test_dispose_engine_clears_state(self) -> None:
        settings = DatabaseSettings()
        init_engine(settings)
        asyncio.run(dispose_engine())

        with pytest.raises(RuntimeError, match="not initialized"):
            get_engine()

    def test_dispose_engine_noop_when_not_initialized(self) -> None:
        # Should not raise
        asyncio.run(dispose_engine())


# ---------------------------------------------------------------------------
# Pool health tests
# ---------------------------------------------------------------------------


class TestPoolHealth:
    """Validate check_pool_health introspection."""

    def setup_method(self) -> None:
        _reset_engine_state()

    def teardown_method(self) -> None:
        _reset_engine_state()

    def test_not_initialized(self) -> None:
        result = check_pool_health()
        assert result == {"initialized": False}

    def test_initialized_returns_pool_stats(self) -> None:
        settings = DatabaseSettings()
        init_engine(settings)

        result = check_pool_health()
        assert result["initialized"] is True
        assert "pool_size" in result
        assert "checked_in" in result
        assert "checked_out" in result
        assert "overflow" in result
        assert "status" in result

        asyncio.run(dispose_engine())

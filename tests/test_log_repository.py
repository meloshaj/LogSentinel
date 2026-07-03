import asyncio
from datetime import datetime, timezone

from backend.app.repositories.db_health import check_database_health, find_missing_columns
from backend.app.repositories.log_repository import LogRepository
from backend.app.services.batch_manager import ParsedLogBatchManager


def test_log_repository_maps_parsed_log_to_insert_row() -> None:
    parsed_at = "2026-07-03T20:00:00+00:00"
    timestamp = "2026-07-03T19:59:59+00:00"

    row = LogRepository.map_parsed_log(
        {
            "raw_message": "service-a failed to connect to 10.0.0.1 on port 5432",
            "template_id": "7",
            "template_text": "service-a failed to connect to <IP> on <PORT>",
            "parameters": [{"value": "10.0.0.1", "mask_name": "IP"}],
            "metadata": {
                "timestamp": timestamp,
                "service": "service-a",
                "level": "error",
                "source": "unit-test",
                "environment": "test",
                "correlation_id": "corr-1",
            },
            "parsed_at": parsed_at,
        }
    )

    assert row["service"] == "service-a"
    assert row["raw_message"] == "service-a failed to connect to 10.0.0.1 on port 5432"
    assert row["template_id"] == "7"
    assert row["template_text"] == "service-a failed to connect to <IP> on <PORT>"
    assert row["parameters"] == [{"value": "10.0.0.1", "mask_name": "IP"}]
    assert row["level"] == "error"
    assert row["source"] == "unit-test"
    assert row["environment"] == "test"
    assert row["correlation_id"] == "corr-1"
    assert row["timestamp"] == datetime.fromisoformat(timestamp)
    assert row["parsed_at"] == datetime.fromisoformat(parsed_at)
    assert row["created_at"].tzinfo is not None


def test_log_repository_mapping_uses_safe_defaults() -> None:
    row = LogRepository.map_parsed_log({})

    assert row["service"] == "unknown"
    assert row["raw_message"] == ""
    assert row["template_id"] == ""
    assert row["parameters"] == []
    assert row["metadata"] == {}
    assert row["timestamp"].tzinfo is not None


def test_batch_manager_with_fake_async_repository_sink() -> None:
    inserted_batches: list[list[dict]] = []

    async def fake_insert(batch: list[dict]) -> int:
        inserted_batches.append(batch)
        return len(batch)

    manager = ParsedLogBatchManager(batch_size=2, sink=fake_insert)

    async def run() -> None:
        await manager.add({"raw_message": "first", "template_id": "1"})
        await manager.add({"raw_message": "second", "template_id": "1"})

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["last_sink_result"] == 2
    assert stats["flushed_record_count"] == 2
    assert len(inserted_batches) == 1


def test_find_missing_columns_reports_absent_required_columns() -> None:
    missing = find_missing_columns({"id", "service", "raw_message"})

    assert "template_id" in missing
    assert "created_at" in missing
    assert "id" not in missing


class FakeScalarResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value

    def __iter__(self):
        return iter(())


class FakeColumnResult:
    def __init__(self, columns: set[str]) -> None:
        self.columns = columns

    def __iter__(self):
        return iter((column,) for column in self.columns)


class FakeConnection:
    def __init__(self, table_exists: bool, columns: set[str]) -> None:
        self.table_exists = table_exists
        self.columns = columns

    async def execute(self, statement):
        statement_text = str(statement)
        if "information_schema.tables" in statement_text:
            return FakeScalarResult(self.table_exists)
        if "information_schema.columns" in statement_text:
            return FakeColumnResult(self.columns)
        raise AssertionError(f"unexpected statement: {statement_text}")


class FakeConnectContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeEngine:
    def __init__(self, table_exists: bool = True, columns: set[str] | None = None, error: Exception | None = None):
        self.table_exists = table_exists
        self.columns = columns or set()
        self.error = error

    def connect(self):
        if self.error:
            raise self.error
        return FakeConnectContext(FakeConnection(self.table_exists, self.columns))


def test_check_database_health_reports_healthy_schema() -> None:
    from backend.app.repositories.db_health import REQUIRED_LOG_COLUMNS

    health = asyncio.run(check_database_health(FakeEngine(columns=set(REQUIRED_LOG_COLUMNS))))

    assert health == {
        "connected": True,
        "table_exists": True,
        "missing_columns": [],
        "error": None,
    }


def test_check_database_health_reports_missing_table() -> None:
    health = asyncio.run(check_database_health(FakeEngine(table_exists=False)))

    assert health["connected"] is True
    assert health["table_exists"] is False
    assert "template_id" in health["missing_columns"]


def test_check_database_health_reports_connection_error() -> None:
    health = asyncio.run(check_database_health(FakeEngine(error=OSError("database down"))))

    assert health["connected"] is False
    assert health["table_exists"] is False
    assert "OSError: database down" == health["error"]

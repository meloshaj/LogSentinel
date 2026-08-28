import asyncio
from datetime import datetime, timezone

from backend.app.models import ParsedLog
from backend.app.repositories.db_health import check_database_health, find_missing_columns
from backend.app.repositories.log_repository import LogRepository


EVENT_TIMESTAMP = datetime(2026, 7, 22, 18, 30, tzinfo=timezone.utc)
METADATA_TIMESTAMP = datetime(2020, 1, 2, 3, 4, tzinfo=timezone.utc)
PARSED_AT = datetime(2026, 7, 22, 18, 31, tzinfo=timezone.utc)


def test_log_repository_maps_canonical_parsed_log_fields(make_parsed_log) -> None:
    log = make_parsed_log(
        timestamp=EVENT_TIMESTAMP,
        service="service-a",
        level="error",
        raw_message="service-a failed to connect to 10.0.0.7 on port 5432",
        template_id="7",
        template_text="service-a failed to connect to <IP> on <PORT>",
        parameters=[{"value": "10.0.0.7", "mask_name": "IP"}],
        cluster_size=12,
        change_type="none",
        source="unit-test",
        environment="test",
        correlation_id="corr-7",
        metadata={
            "timestamp": METADATA_TIMESTAMP.isoformat(),
            "observed_at": METADATA_TIMESTAMP,
            "custom": {"attempt": 7},
        },
        parsed_at=PARSED_AT,
    )
    row = LogRepository.map_parsed_log(log)

    assert row["service"] == "service-a"
    assert row["raw_message"] == "service-a failed to connect to 10.0.0.7 on port 5432"
    assert row["template_id"] == "7"
    assert row["template_text"] == "service-a failed to connect to <IP> on <PORT>"
    assert row["parameters"] == [{"value": "10.0.0.7", "mask_name": "IP"}]
    assert row["level"] == "error"
    assert row["source"] == "unit-test"
    assert row["environment"] == "test"
    assert row["correlation_id"] == "corr-7"
    assert row["parsed_at"] is PARSED_AT
    assert row["created_at"].tzinfo is not None


def test_top_level_timestamp_is_canonical_over_metadata_timestamp(make_parsed_log) -> None:
    log = make_parsed_log(
        timestamp=EVENT_TIMESTAMP,
        metadata={"timestamp": METADATA_TIMESTAMP.isoformat()}
    )
    row = LogRepository.map_parsed_log(log)

    assert row["timestamp"] is EVENT_TIMESTAMP
    assert row["timestamp"] != datetime.fromisoformat(log.metadata["timestamp"])


def test_json_fields_are_serialized_without_mutating_parsed_log(make_parsed_log) -> None:
    log = make_parsed_log(
        metadata={"observed_at": METADATA_TIMESTAMP}
    )
    original = log.model_copy(deep=True)
    row = LogRepository.map_parsed_log(log)

    assert row["parameters"] == log.parameters
    assert row["parameters"] is not log.parameters
    assert row["metadata"]["observed_at"] == "2020-01-02T03:04:00Z"
    assert row["metadata"] is not log.metadata
    assert log == original
    assert log.metadata["observed_at"] is METADATA_TIMESTAMP


def test_runtime_only_fields_are_not_in_insert_row(make_parsed_log) -> None:
    row = LogRepository.map_parsed_log(make_parsed_log(cluster_size=12, change_type="none"))

    assert "cluster_size" not in row
    assert "change_type" not in row


class FakeAsyncpgConnection:
    def __init__(self):
        pass
    async def copy_records_to_table(self, *args, **kwargs):
        pass

class FakeRawConnection:
    def __init__(self):
        self.driver_connection = FakeAsyncpgConnection()

class FakeInsertConnection:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[object, list[dict[str, object]] | None]] = []

    async def get_raw_connection(self):
        return FakeRawConnection()

    async def execute(
        self,
        statement: object,
        *args,
        **kwargs,
    ) -> None:
        rows = args[0] if args else kwargs.get("parameters")
        self.execute_calls.append((statement, rows))
        
    async def commit(self) -> None:
        pass


class FakeBeginContext:
    def __init__(self, connection: FakeInsertConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeInsertConnection:
        return self.connection

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeInsertEngine:
    def __init__(self) -> None:
        self.begin_count = 0
        self.connection = FakeInsertConnection()

    def connect(self) -> FakeBeginContext:
        self.begin_count += 1
        return FakeBeginContext(self.connection)


def test_bulk_insert_uses_one_transaction_for_typed_batch(make_parsed_log) -> None:
    engine = FakeInsertEngine()
    repository = LogRepository(engine=engine)  # type: ignore[arg-type]
    logs = [
        make_parsed_log(template_id="1", timestamp=EVENT_TIMESTAMP),
        make_parsed_log(template_id="2", timestamp=EVENT_TIMESTAMP)
    ]

    inserted = asyncio.run(repository.bulk_insert_parsed_logs(logs))

    assert inserted == 2
    assert engine.begin_count == 1
    assert len(engine.connection.execute_calls) == 1
    _, rows = engine.connection.execute_calls[0]
    assert [row["template_id"] for row in rows] == ["1", "2"]
    assert all(row["timestamp"] is EVENT_TIMESTAMP for row in rows)
    assert all(isinstance(log, ParsedLog) for log in logs)


def test_bulk_insert_empty_input_skips_transaction() -> None:
    engine = FakeInsertEngine()
    repository = LogRepository(engine=engine)  # type: ignore[arg-type]

    inserted = asyncio.run(repository.bulk_insert_parsed_logs([]))

    assert inserted == 0
    assert engine.begin_count == 0
    assert engine.connection.execute_calls == []


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

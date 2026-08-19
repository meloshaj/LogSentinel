from backend.app.services.drain_parser import DrainParser
import pytest
from unittest.mock import patch, MagicMock

@pytest.fixture(autouse=True)
def mock_redis():
    with patch("backend.app.services.drain_parser.redis.Redis") as mock:
        mock.return_value = MagicMock()
        # Ensure it raises an exception when attempting to use RedisPersistence to force FilePersistence fallback
        mock.side_effect = Exception("Mocked Redis unavailable")
        yield mock

def make_parser(tmp_path):
    return DrainParser(state_path=str(tmp_path / "drain3_state.bin"))


def test_drain_parser_can_initialize(tmp_path) -> None:
    parser = make_parser(tmp_path)

    stats = parser.get_stats()

    assert stats["cluster_count"] == 0
    assert stats["state_path"].endswith("drain3_state.bin")


def test_similar_logs_produce_same_template(tmp_path) -> None:
    parser = make_parser(tmp_path)

    first = parser.parse("service-a failed to connect to 10.0.0.1 on port 5432")
    second = parser.parse("service-a failed to connect to 10.0.0.2 on port 5432")

    assert first["template_id"] == second["template_id"] or first["template_text"] == second["template_text"]


def test_different_log_types_produce_different_templates(tmp_path) -> None:
    parser = make_parser(tmp_path)

    connection = parser.parse("service-a failed to connect to 10.0.0.1 on port 5432")
    login = parser.parse("user blerim logged in from 192.168.1.5")
    parser.parse("user admin logged in from 192.168.1.6")

    assert connection["template_text"] != login["template_text"]


def test_parse_returns_expected_fields(tmp_path) -> None:
    parser = make_parser(tmp_path)
    metadata = {"source": "unit-test"}

    result = parser.parse("user blerim logged in from 192.168.1.5", metadata=metadata)

    assert result.raw_message == "user blerim logged in from 192.168.1.5"
    assert result.metadata == metadata
    assert isinstance(result.parameters, list)

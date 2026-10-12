import os
from pathlib import Path

import pytest

from scripts.safe_env_file import parse_env_text, replace_env_value, update_env_file


def test_safe_dotenv_round_trip_preserves_special_characters_without_execution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = tmp_path / "must-not-exist"
    fake_password = (
        f" leading space $HOME ! # \"double quote\" 'single quote' "
        f"\\\\ café $(touch {marker})"
    )
    source = "FIRST=one\r\nSMTP_PASSWORD=old  # keep this note\r\nLAST=two\r\n"

    updated = replace_env_value(source, "SMTP_PASSWORD", fake_password)

    assert parse_env_text(updated)["SMTP_PASSWORD"] == fake_password
    assert updated.startswith("FIRST=one\r\n")
    assert updated.endswith("LAST=two\r\n")
    assert "  # keep this note\r\n" in updated
    assert not marker.exists()
    assert capsys.readouterr().out == ""


def test_atomic_update_preserves_mode_and_unrelated_entries(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"KEEP=unchanged\r\nSMTP_PASSWORD=old\r\nTAIL=value\r\n")
    env_file.chmod(0o600)
    expected = "new $ value ! # 'quoted' " + chr(92)

    update_env_file(env_file, "SMTP_PASSWORD", expected)

    content = env_file.read_bytes()
    parsed = parse_env_text(content.decode("utf-8"))
    assert content.startswith(b"KEEP=unchanged\r\n")
    assert content.endswith(b"TAIL=value\r\n")
    assert parsed["SMTP_PASSWORD"] == expected
    if os.name != "nt":
        assert env_file.stat().st_mode & 0o777 == 0o600


def test_parser_rejects_malformed_quoted_assignment() -> None:
    with pytest.raises(ValueError, match="line 1"):
        parse_env_text("SMTP_PASSWORD='unterminated\n")


def test_update_refuses_duplicate_keys_and_multiline_values() -> None:
    with pytest.raises(ValueError, match="more than once"):
        replace_env_value("SMTP_PASSWORD=one\nSMTP_PASSWORD=two\n", "SMTP_PASSWORD", "x")
    with pytest.raises(ValueError, match="single-line"):
        replace_env_value("SMTP_PASSWORD=old\n", "SMTP_PASSWORD", "line one\nline two")

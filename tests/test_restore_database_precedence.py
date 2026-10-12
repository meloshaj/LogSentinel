from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.pitr_storage import get_storage_config

ROOT = Path(__file__).resolve().parents[1]


def test_logical_restore_prefers_backup_bucket_before_archive_fallback() -> None:
    source = (ROOT / "scripts" / "restore_database.sh").read_text(encoding="utf-8")
    assert (
        'S3_BUCKET="${S3_BUCKET:-${S3_BACKUP_BUCKET:-${S3_BUCKET_NAME:-}}}"'
        in source
    )


def _set_recovery_storage_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("S3_", "AWS_")) or name in {
            "ENVIRONMENT",
            "PITR_REQUIRE_CREDENTIALS",
        }:
            monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PITR_REQUIRE_CREDENTIALS", "true")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("S3_BACKUP_BUCKET", "approved-backups")
    monkeypatch.setenv("S3_BUCKET_NAME", "cold-archive")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://objects.example.test")
    monkeypatch.setenv("S3_REGION", "eu-central-003")


@pytest.mark.parametrize(
    ("kind", "bucket_variable", "expected_prefix"),
    [
        ("wal", "S3_WAL_BUCKET", "logsentinel/wal/"),
        ("base", "S3_BASE_BUCKET", "logsentinel/pitr-base/"),
    ],
)
def test_recovery_storage_prefers_shared_backup_bucket_to_archive_bucket(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    bucket_variable: str,
    expected_prefix: str,
) -> None:
    _set_recovery_storage_environment(monkeypatch)

    config = get_storage_config(kind)

    assert config.bucket == "approved-backups"
    assert config.bucket != "cold-archive"
    assert config.endpoint == "https://objects.example.test"
    assert config.region == "eu-central-003"
    assert config.prefix == expected_prefix
    assert not os.getenv(bucket_variable)


@pytest.mark.parametrize(
    ("kind", "bucket_variable"),
    [("wal", "S3_WAL_BUCKET"), ("base", "S3_BASE_BUCKET")],
)
def test_recovery_storage_preserves_purpose_specific_bucket_override(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    bucket_variable: str,
) -> None:
    _set_recovery_storage_environment(monkeypatch)
    monkeypatch.setenv(bucket_variable, "purpose-specific-recovery")

    config = get_storage_config(kind)

    assert config.bucket == "purpose-specific-recovery"

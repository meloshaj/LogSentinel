from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from scripts import wal_archive, wal_restore
from scripts.pitr_storage import StorageOperationError


WAL = "000000010000000000000001"
BACKUP_HISTORY = "00000001000000000000000E.00000028.backup"
TIMELINE_HISTORY = "00000002.history"


class ProviderError(RuntimeError):
    def __init__(self, message: str, code: str = "500") -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code}}


class ConnectionClosedError(RuntimeError):
    """Provider behavior used to exercise the non-conditional PUT fallback."""


class SSLError(RuntimeError):
    """Provider TLS-level rejection used by some conditional PUT endpoints."""


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        del Bucket
        if Key not in self.objects:
            raise ProviderError("missing object", "404")
        value, metadata = self.objects[Key]
        return {"ContentLength": len(value), "Metadata": metadata}

    def put_object(self, *, Bucket: str, Key: str, Body: Any, **kwargs: Any) -> None:
        del Bucket
        if Key in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise ProviderError("conditional write conflict", "412")
        value = Body.read()
        metadata = {str(key): str(item) for key, item in kwargs["Metadata"].items()}
        self.objects[Key] = (value, metadata)

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        del Bucket
        Path(Filename).write_bytes(self.objects[Key][0])

    def get_paginator(self, name: str) -> Any:
        assert name == "list_objects_v2"

        class Paginator:
            def __init__(
                self, objects: dict[str, tuple[bytes, dict[str, str]]]
            ) -> None:
                self.objects = objects

            def paginate(self, *, Bucket: str, Prefix: str) -> list[dict[str, Any]]:
                del Bucket
                return [
                    {
                        "Contents": [
                            {
                                "Key": key,
                                "Size": len(value),
                                "LastModified": "2026-09-16T12:00:00Z",
                            }
                            for key, (value, _) in self.objects.items()
                            if key.startswith(Prefix)
                        ]
                    }
                ]

        return Paginator(self.objects)


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("PITR_REQUIRE_CREDENTIALS", "false")
    monkeypatch.setenv("S3_WAL_BUCKET", "recovery-bucket")
    monkeypatch.setenv("S3_WAL_REGION", "us-east-1")
    monkeypatch.setenv("S3_WAL_ENDPOINT", "https://objects.example.test")
    monkeypatch.setenv("S3_WAL_PREFIX", "logsentinel/wal/")


def test_successful_archive_uploads_verified_safe_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / WAL
    source.write_bytes(b"wal-segment")
    client = FakeS3()

    result = wal_archive.archive_wal(source, WAL, client=client)

    assert result["status"] == "uploaded"
    key = f"logsentinel/wal/{WAL}"
    assert key in client.objects
    assert (
        client.objects[key][1]["sha256"] == hashlib.sha256(b"wal-segment").hexdigest()
    )
    assert client.objects[key][1]["wal_filename"] == WAL


def test_missing_wal_source_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)

    with pytest.raises(StorageOperationError, match="missing or empty"):
        wal_archive.archive_wal(tmp_path / WAL, WAL, client=FakeS3())


def test_remote_upload_failure_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / WAL
    source.write_bytes(b"wal-segment")

    class FailingClient(FakeS3):
        def put_object(self, **kwargs: Any) -> None:
            raise ProviderError("secret-access-key=do-not-print", "403")

    with pytest.raises(StorageOperationError) as failure:
        wal_archive.archive_wal(source, WAL, client=FailingClient())
    assert "do-not-print" not in str(failure.value)
    assert "secret-access-key" not in str(failure.value)


def test_remote_verification_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / WAL
    source.write_bytes(b"wal-segment")

    class BadHeadClient(FakeS3):
        def head_object(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["Key"] in self.objects:
                return {"ContentLength": 999, "Metadata": {"sha256": "0" * 64}}
            return super().head_object(**kwargs)

    with pytest.raises(StorageOperationError, match="integrity mismatch"):
        wal_archive.archive_wal(source, WAL, client=BadHeadClient())


def test_duplicate_same_wal_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / WAL
    source.write_bytes(b"wal-segment")
    client = FakeS3()

    wal_archive.archive_wal(source, WAL, client=client)
    result = wal_archive.archive_wal(source, WAL, client=client)

    assert result["status"] == "idempotent"


def test_provider_without_conditional_put_uses_verified_preflight_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / WAL
    source.write_bytes(b"wal-segment")

    class PreconditionUnsupportedClient(FakeS3):
        def put_object(
            self, *, Bucket: str, Key: str, Body: Any, **kwargs: Any
        ) -> None:
            if kwargs.get("IfNoneMatch") == "*":
                raise ConnectionClosedError("provider closed conditional request")
            super().put_object(Bucket=Bucket, Key=Key, Body=Body, **kwargs)

    result = wal_archive.archive_wal(
        source, WAL, client=PreconditionUnsupportedClient()
    )

    assert result["status"] == "uploaded"
    assert result["write_mode"] == "preflight"


def test_provider_tls_error_on_conditional_put_uses_verified_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / WAL
    source.write_bytes(b"wal-segment")

    class TlsErrorClient(FakeS3):
        def put_object(
            self, *, Bucket: str, Key: str, Body: Any, **kwargs: Any
        ) -> None:
            if kwargs.get("IfNoneMatch") == "*":
                raise SSLError("provider closed TLS request")
            super().put_object(Bucket=Bucket, Key=Key, Body=Body, **kwargs)

    result = wal_archive.archive_wal(source, WAL, client=TlsErrorClient())

    assert result["status"] == "uploaded"
    assert result["write_mode"] == "preflight"


def test_unsafe_wal_names_and_source_mismatch_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / WAL
    source.write_bytes(b"wal-segment")

    with pytest.raises(Exception, match="supported PostgreSQL archive name"):
        wal_archive.validate_wal_source(source, "../../credentials")
    with pytest.raises(Exception, match="source and archive filename"):
        wal_archive.validate_wal_source(source, "000000010000000000000002")


def test_restore_downloads_and_verifies_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / WAL
    source.write_bytes(b"wal-segment")
    client = FakeS3()
    wal_archive.archive_wal(source, WAL, client=client)

    destination = tmp_path / "restore" / WAL
    result = wal_restore.restore_wal(WAL, destination, client=client)

    assert result["size"] == source.stat().st_size
    assert destination.read_bytes() == source.read_bytes()


def test_postgresql_backup_history_filename_is_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / BACKUP_HISTORY
    source.write_bytes(b"backup-history")
    client = FakeS3()

    result = wal_archive.archive_wal(source, BACKUP_HISTORY, client=client)

    assert result["status"] == "uploaded"
    assert f"logsentinel/wal/{BACKUP_HISTORY}" in client.objects


def test_postgresql_timeline_history_filename_is_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    source = tmp_path / TIMELINE_HISTORY
    source.write_bytes(b"timeline-history")
    client = FakeS3()

    result = wal_archive.archive_wal(source, TIMELINE_HISTORY, client=client)

    assert result["status"] == "uploaded"
    assert f"logsentinel/wal/{TIMELINE_HISTORY}" in client.objects

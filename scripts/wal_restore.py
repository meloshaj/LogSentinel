"""Retrieve one PostgreSQL WAL segment from verified remote object storage."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        download_file_verified,
        get_storage_config,
        make_client,
        safe_key,
    )
except ImportError:  # pragma: no cover - direct container-script execution
    from pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        download_file_verified,
        get_storage_config,
        make_client,
        safe_key,
    )


WAL_FILENAME = re.compile(
    r"^(?:"
    r"[0-9A-Fa-f]{24}|"
    r"[0-9A-Fa-f]{8}\.history|"
    r"[0-9A-Fa-f]{24}\.[0-9A-Fa-f]{8}\.backup"
    r")$"
)
UTC = dt.timezone.utc
LOCAL_READ_FAILURES = {
    "recovery file could not be read",
    "downloaded recovery object is unavailable",
}


def _utc_now() -> str:
    return dt.datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _classification(filename: str) -> str:
    if re.fullmatch(r"[0-9A-Fa-f]{8}\.history", filename):
        return "timeline_history"
    if re.fullmatch(r"[0-9A-Fa-f]{24}\.[0-9A-Fa-f]{8}\.backup", filename):
        return "backup_history"
    if re.fullmatch(r"[0-9A-Fa-f]{24}", filename):
        return "wal_segment"
    return "invalid"


@dataclass
class RestoreObservation:
    filename: str
    classification: str = field(init=False)
    request_utc: str = field(default_factory=_utc_now)
    lookup_result: str = "NOT_ATTEMPTED"
    download_result: str = "NOT_ATTEMPTED"
    integrity_result: str = "NOT_CHECKED"
    destination_result: str = "NOT_ATTEMPTED"
    result: str = "IN_PROGRESS"
    helper_exit_status: int | None = None
    failure_stage: str = "none"

    def __post_init__(self) -> None:
        self.classification = _classification(self.filename)

    def emit(self, phase: str) -> None:
        safe_filename = (
            self.filename
            if WAL_FILENAME.fullmatch(self.filename)
            else "<invalid-filename>"
        )
        record = {
            "event": "wal_restore_request",
            "phase": phase,
            "request_utc": self.request_utc,
            "event_utc": _utc_now(),
            "requested_filename": safe_filename,
            "classification": self.classification,
            "lookup_result": self.lookup_result,
            "download_result": self.download_result,
            "integrity_result": self.integrity_result,
            "destination_result": self.destination_result,
            "result": self.result,
            "failure_stage": self.failure_stage,
            "helper_exit_status": self.helper_exit_status,
        }
        print(
            "WAL_RESTORE_EVENT "
            + json.dumps(record, sort_keys=True, separators=(",", ":")),
            file=sys.stderr,
            flush=True,
        )

    def complete(
        self, result: str, exit_status: int, failure_stage: str = "none"
    ) -> None:
        self.result = result
        self.helper_exit_status = exit_status
        self.failure_stage = failure_stage
        self.emit("complete")


def _is_not_found(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    if isinstance(error, dict) and str(error.get("Code", "")) in {
        "404",
        "NoSuchKey",
        "NotFound",
    }:
        return True
    metadata = response.get("ResponseMetadata")
    return (
        isinstance(metadata, dict)
        and str(metadata.get("HTTPStatusCode", "")) == "404"
    )


class _ObservedStorageClient:
    """Observe only the two storage calls used by verified WAL retrieval."""

    def __init__(self, client: Any, observation: RestoreObservation) -> None:
        self._client = client
        self._observation = observation

    def head_object(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = self._client.head_object(*args, **kwargs)
        except Exception as exc:
            self._observation.lookup_result = (
                "NOT_FOUND" if _is_not_found(exc) else "LOOKUP_FAILED"
            )
            self._observation.emit("lookup")
            raise
        self._observation.lookup_result = "FOUND"
        self._observation.emit("lookup")
        return result

    def download_file(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = self._client.download_file(*args, **kwargs)
        except Exception:
            self._observation.download_result = "DOWNLOAD_FAILED"
            self._observation.destination_result = "DOWNLOAD_FAILED"
            self._observation.emit("download")
            raise
        self._observation.download_result = "PASS"
        self._observation.destination_result = "WRITTEN_UNVERIFIED"
        self._observation.emit("download")
        return result


def restore_wal(
    filename: str,
    destination: Path,
    *,
    client: Any | None = None,
    observation: RestoreObservation | None = None,
) -> dict[str, Any]:
    if not WAL_FILENAME.fullmatch(filename):
        raise StorageConfigError(
            "WAL filename is not a supported PostgreSQL archive name"
        )
    config = get_storage_config("wal")
    key = safe_key(config, filename)
    if observation is None:
        return download_file_verified(config, key, destination, client=client)
    observed_client = _ObservedStorageClient(client or make_client(config), observation)
    return download_file_verified(config, key, destination, client=observed_client)


def _failure_state(
    observation: RestoreObservation, exc: Exception
) -> tuple[str, str]:
    if isinstance(exc, StorageConfigError):
        if observation.classification == "invalid":
            return "INVALID_REQUEST", "request_validation"
        return "CONFIG_FAILED", "storage_configuration"

    if isinstance(exc, OSError):
        observation.destination_result = "WRITE_FAILED"
        return "DOWNLOAD_FAILED", "destination_write"
    if isinstance(exc, StorageOperationError):
        message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
        if observation.lookup_result == "NOT_FOUND":
            return "NOT_FOUND", "lookup"
        if observation.lookup_result == "LOOKUP_FAILED":
            return "DOWNLOAD_FAILED", "lookup"
        if observation.download_result == "DOWNLOAD_FAILED":
            return "DOWNLOAD_FAILED", "download"
        if observation.download_result == "PASS":
            if message in LOCAL_READ_FAILURES:
                observation.destination_result = "READ_FAILED"
                return "DOWNLOAD_FAILED", "destination_read"
            observation.integrity_result = "VERIFY_FAILED"
            return "VERIFY_FAILED", "integrity"
        if observation.lookup_result == "FOUND":
            observation.integrity_result = "VERIFY_FAILED"
            return "VERIFY_FAILED", "integrity"
        return "DOWNLOAD_FAILED", "storage_client"
    return "INTERNAL_FAILED", "internal"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filename", help="PostgreSQL %%f restore filename")
    parser.add_argument(
        "destination", type=Path, help="PostgreSQL %%p restore destination"
    )
    args = parser.parse_args(argv)
    observation = RestoreObservation(args.filename)
    observation.emit("request")
    try:
        result = restore_wal(
            args.filename, args.destination, observation=observation
        )
    except (StorageConfigError, StorageOperationError) as exc:
        # PostgreSQL retries a nonzero restore_command result.  Keep this path
        # safe even when an object-store provider exception is credential-rich.
        outcome, failure_stage = _failure_state(observation, exc)
        observation.complete(outcome, 1, failure_stage)
        print("WAL restore failed: object unavailable or unverifiable", file=sys.stderr)
        return 1
    except Exception as exc:
        outcome, failure_stage = _failure_state(observation, exc)
        observation.complete(outcome, 1, failure_stage)
        print("WAL restore failed: unexpected internal error", file=sys.stderr)
        return 1
    observation.integrity_result = "PASS"
    observation.destination_result = "PASS"
    observation.complete("PASS", 0)
    print(
        "WAL restore verified: "
        f"filename={args.filename} size={result['size']} sha256={result['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

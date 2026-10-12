"""Secret-safe S3-compatible storage primitives for PostgreSQL recovery.

The helpers in this module deliberately expose only object names, sizes,
checksums, and safe error categories.  Provider exception text is never
returned to callers because S3-compatible clients can include request URLs or
credential material in exception messages.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class StorageConfigError(RuntimeError):
    """Raised when recovery storage configuration is incomplete or unsafe."""


class StorageOperationError(RuntimeError):
    """Raised when a remote recovery object cannot be verified safely."""


@dataclass(frozen=True)
class StorageConfig:
    """Resolved S3-compatible configuration without serializing secrets."""

    bucket: str
    endpoint: str | None
    region: str
    access_key_id: str | None
    secret_access_key: str | None
    prefix: str
    server_side_encryption: str


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _first_configured(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _require_tls(endpoint: str | None) -> None:
    if not endpoint:
        return
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise StorageConfigError("recovery storage endpoint must be an HTTPS origin")


def normalize_prefix(value: str, default: str) -> str:
    """Return a safe logical prefix with exactly one trailing slash."""

    prefix = (value or default).strip().strip("/")
    parts = prefix.split("/")
    if not prefix or any(part in {"", ".", ".."} for part in parts):
        raise StorageConfigError("recovery storage prefix is invalid")
    if any(not _SAFE_COMPONENT.fullmatch(part) for part in parts):
        raise StorageConfigError("recovery storage prefix contains unsafe characters")
    return "/".join(parts) + "/"


def get_storage_config(kind: str) -> StorageConfig:
    """Resolve WAL or physical-base-backup storage from existing S3 settings.

    A dedicated ``S3_WAL_*`` or ``S3_BASE_*`` value wins when present.  The
    existing backup bucket/endpoint/region/credential names are the supported
    fallback so production can reuse its already-authorized object-storage
    infrastructure while keeping recovery objects under distinct prefixes.
    """

    normalized_kind = kind.strip().lower()
    if normalized_kind not in {"wal", "base"}:
        raise StorageConfigError("unsupported recovery storage kind")

    upper_kind = normalized_kind.upper()
    bucket = _first_configured(
        f"S3_{upper_kind}_BUCKET",
        "S3_BACKUP_BUCKET",
        "S3_BUCKET",
        "S3_BUCKET_NAME",
    )
    endpoint = _first_configured(
        f"S3_{upper_kind}_ENDPOINT",
        "S3_ENDPOINT",
        "S3_ENDPOINT_URL",
    )
    region = _first_configured(
        f"S3_{upper_kind}_REGION",
        "S3_REGION",
    )
    access_key_id = _first_configured(
        f"S3_{upper_kind}_ACCESS_KEY_ID",
        "S3_ACCESS_KEY_ID",
    )
    secret_access_key = _first_configured(
        f"S3_{upper_kind}_SECRET_ACCESS_KEY",
        "S3_SECRET_ACCESS_KEY",
    )
    prefix = normalize_prefix(
        os.getenv(f"S3_{upper_kind}_PREFIX", ""),
        f"logsentinel/{'wal' if normalized_kind == 'wal' else 'pitr-base'}",
    )
    server_side_encryption = os.getenv(
        f"S3_{upper_kind}_SERVER_SIDE_ENCRYPTION",
        os.getenv("S3_SERVER_SIDE_ENCRYPTION", "AES256"),
    ).strip()

    if not bucket:
        raise StorageConfigError("recovery storage bucket is required")
    if not region:
        raise StorageConfigError("recovery storage region is required")
    if not server_side_encryption:
        raise StorageConfigError("recovery storage encryption mode is required")
    _require_tls(endpoint)

    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    require_credentials = os.getenv("PITR_REQUIRE_CREDENTIALS", "")
    if require_credentials == "":
        require_credentials = "true" if environment == "production" else "false"
    if (
        require_credentials.lower() == "true"
        and not access_key_id
        and not secret_access_key
    ):
        raise StorageConfigError("recovery storage credentials are required")
    if bool(access_key_id) != bool(secret_access_key):
        raise StorageConfigError("recovery storage credentials are incomplete")

    return StorageConfig(
        bucket=bucket,
        endpoint=endpoint,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        prefix=prefix,
        server_side_encryption=server_side_encryption,
    )


def make_client(config: StorageConfig) -> Any:
    """Create a bounded boto3 client without enabling unsafe metadata probes."""

    try:
        import boto3
        from botocore.config import Config
    except Exception as exc:  # pragma: no cover - image packaging failure
        raise StorageOperationError("boto3 client is unavailable") from exc

    try:
        return boto3.client(
            "s3",
            endpoint_url=config.endpoint,
            region_name=config.region,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            config=Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"mode": "standard", "total_max_attempts": 3},
                max_pool_connections=4,
            ),
        )
    except Exception as exc:
        raise StorageOperationError(
            f"storage client creation failed ({type(exc).__name__})"
        ) from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise StorageOperationError("recovery file could not be read") from exc
    return digest.hexdigest()


def safe_key(config: StorageConfig, *components: str) -> str:
    """Build a key from validated, non-traversing object-name components."""

    if not components or any(
        not _SAFE_COMPONENT.fullmatch(part) for part in components
    ):
        raise StorageConfigError("recovery object name is invalid")
    return config.prefix + "/".join(components)


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            code = error.get("Code")
            if code is not None:
                return str(code)
        metadata = response.get("ResponseMetadata")
        if isinstance(metadata, dict) and metadata.get("HTTPStatusCode") is not None:
            return str(metadata["HTTPStatusCode"])
    return ""


def _supports_conditional_put(exc: BaseException) -> bool:
    """Return whether a failed conditional write should use the safe fallback."""

    # The production S3-compatible endpoint closes the connection when it sees
    # If-None-Match on PutObject.  Keep this allowlist narrow: other provider
    # failures must remain hard failures rather than silently becoming writes.
    return type(exc).__name__ in {"ConnectionClosedError", "SSLError"} or _error_code(
        exc
    ) in {
        "NotImplemented",
        "NotImplementedException",
    }


def _head(client: Any, config: StorageConfig, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=config.bucket, Key=key)
    except Exception as exc:
        if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise StorageOperationError(
            f"remote object head failed ({type(exc).__name__})"
        ) from exc


def _metadata(head: dict[str, Any]) -> dict[str, str]:
    raw = head.get("Metadata") or {}
    return {str(key).lower(): str(value) for key, value in raw.items()}


def verify_head(head: dict[str, Any] | None, *, size: int, digest: str) -> None:
    """Verify the exact byte size and SHA-256 metadata for an object."""

    if head is None:
        raise StorageOperationError("remote object is missing")
    try:
        remote_size = int(head.get("ContentLength", -1))
    except (TypeError, ValueError) as exc:
        raise StorageOperationError("remote object size metadata is invalid") from exc
    if remote_size != size or _metadata(head).get("sha256") != digest:
        raise StorageOperationError("remote object integrity mismatch")


def upload_file_verified(
    config: StorageConfig,
    path: Path,
    key: str,
    *,
    content_type: str = "application/octet-stream",
    metadata: dict[str, str] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Upload one object with collision-safe idempotency and post-upload HEAD."""

    if not path.is_file() or not path.stat().st_size:
        raise StorageOperationError("recovery source file is missing or empty")
    digest = sha256_file(path)
    size = path.stat().st_size
    expected_metadata = {**(metadata or {}), "sha256": digest}
    storage = client or make_client(config)

    existing = _head(storage, config, key)
    if existing is not None:
        verify_head(existing, size=size, digest=digest)
        return {
            "status": "idempotent",
            "write_mode": "none",
            "key": key,
            "size": size,
            "sha256": digest,
        }

    try:
        with path.open("rb") as source:
            storage.put_object(
                Bucket=config.bucket,
                Key=key,
                Body=source,
                ContentLength=size,
                ContentType=content_type,
                ServerSideEncryption=config.server_side_encryption,
                Metadata=expected_metadata,
                IfNoneMatch="*",
            )
    except Exception as exc:
        if _error_code(exc) in {
            "409",
            "412",
            "ConditionalRequestConflict",
            "PreconditionFailed",
        }:
            raced = _head(storage, config, key)
            if raced is not None:
                verify_head(raced, size=size, digest=digest)
                return {
                    "status": "idempotent",
                    "write_mode": "none",
                    "key": key,
                    "size": size,
                    "sha256": digest,
                }
        if not _supports_conditional_put(exc):
            raise StorageOperationError(
                f"remote object upload failed ({type(exc).__name__})"
            ) from exc
        # Some S3-compatible providers do not implement conditional PUT and
        # close the connection instead of returning a useful error.  Recheck
        # immediately, then use an unconditional PUT only when the object is
        # still absent.  The mandatory exact HEAD below turns any concurrent
        # mismatched write into a visible failure.
        raced = _head(storage, config, key)
        if raced is not None:
            verify_head(raced, size=size, digest=digest)
            return {
                "status": "idempotent",
                "write_mode": "none",
                "key": key,
                "size": size,
                "sha256": digest,
            }
        try:
            with path.open("rb") as source:
                storage.put_object(
                    Bucket=config.bucket,
                    Key=key,
                    Body=source,
                    ContentLength=size,
                    ContentType=content_type,
                    ServerSideEncryption=config.server_side_encryption,
                    Metadata=expected_metadata,
                )
        except Exception as fallback_exc:
            raise StorageOperationError(
                f"remote object upload failed ({type(fallback_exc).__name__})"
            ) from fallback_exc
        verify_head(_head(storage, config, key), size=size, digest=digest)
        return {
            "status": "uploaded",
            "write_mode": "preflight",
            "key": key,
            "size": size,
            "sha256": digest,
        }

    verify_head(_head(storage, config, key), size=size, digest=digest)
    return {
        "status": "uploaded",
        "write_mode": "conditional",
        "key": key,
        "size": size,
        "sha256": digest,
    }


def download_file_verified(
    config: StorageConfig,
    key: str,
    destination: Path,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """Download one object and verify its advertised checksum and size."""

    storage = client or make_client(config)
    head = _head(storage, config, key)
    if head is None:
        raise StorageOperationError("remote recovery object is missing")
    metadata = _metadata(head)
    expected_digest = metadata.get("sha256")
    if not expected_digest or len(expected_digest) != 64:
        raise StorageOperationError(
            "remote recovery object has no valid checksum metadata"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        storage.download_file(config.bucket, key, str(destination))
    except Exception as exc:
        raise StorageOperationError(
            f"remote object download failed ({type(exc).__name__})"
        ) from exc
    try:
        size = destination.stat().st_size
    except OSError as exc:
        raise StorageOperationError(
            "downloaded recovery object is unavailable"
        ) from exc
    digest = sha256_file(destination)
    verify_head(head, size=size, digest=expected_digest)
    if digest != expected_digest:
        raise StorageOperationError("downloaded recovery object checksum mismatch")
    return {"key": key, "size": size, "sha256": digest}


def list_objects(
    config: StorageConfig, *, client: Any | None = None
) -> list[dict[str, Any]]:
    """List safe recovery-object metadata under the configured prefix."""

    storage = client or make_client(config)
    objects: list[dict[str, Any]] = []
    try:
        paginator = storage.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=config.bucket, Prefix=config.prefix):
            for item in page.get("Contents", []):
                key = str(item.get("Key", ""))
                if key.startswith(config.prefix):
                    objects.append(
                        {
                            "key": key,
                            "size": int(item.get("Size", 0)),
                            "last_modified": str(item.get("LastModified", "")),
                        }
                    )
    except Exception as exc:
        raise StorageOperationError(
            f"remote recovery listing failed ({type(exc).__name__})"
        ) from exc
    return objects

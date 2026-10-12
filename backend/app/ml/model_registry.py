"""Tenant-specific immutable model artifacts and atomic promotion."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text

from ..archive.s3_client import get_s3_client, read_object, run_storage_io
from ..core.database import get_engine
from .anomaly_detector import FEATURE_COLUMNS, IsolationForestAnomalyDetector

FEATURE_SCHEMA_VERSION = "logsentinel-feature-schema-v1"
MAX_MODEL_BYTES = 100 * 1024 * 1024


class ModelArtifactStore(Protocol):
    def put_immutable(self, key: str, data: bytes) -> str: ...
    def get(self, uri: str) -> bytes: ...


class LocalModelArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def put_immutable(self, key: str, data: bytes) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
        return str(path.resolve())

    def get(self, uri: str) -> bytes:
        return Path(uri).read_bytes()


class S3ModelArtifactStore:
    def __init__(self) -> None:
        self.client = get_s3_client()

    def put_immutable(self, key: str, data: bytes) -> str:
        if not self.client.put_if_absent(key, data):
            raise FileExistsError("immutable model artifact already exists")
        return f"s3://models/{key}"

    def get(self, uri: str) -> bytes:
        key = uri.split("/", 3)[-1]
        return read_object(self.client, key, MAX_MODEL_BYTES)


def configured_artifact_store(base_path: str | Path) -> ModelArtifactStore:
    backend = os.getenv("MODEL_ARTIFACT_STORE", "local").strip().lower()
    if backend == "s3":
        return S3ModelArtifactStore()
    if backend != "local":
        raise ValueError("MODEL_ARTIFACT_STORE must be 'local' or 's3'")
    return LocalModelArtifactStore(Path(base_path).parent)


def tenant_hash(tenant_id: str, owner_user_id: int) -> str:
    return hashlib.sha256(f"{tenant_id}:{owner_user_id}".encode()).hexdigest()[:32]


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PromotionLock:
    """Small cross-process lock using an exclusive file create."""

    def __init__(self, path: Path, timeout_seconds: float = 30.0) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._fd: int | None = None

    def __enter__(self) -> PromotionLock:
        deadline = time.monotonic() + self.timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"model promotion lock timed out: {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def promote_detector(
    *,
    base_path: str | Path,
    tenant_id: str,
    owner_user_id: int,
    detector: IsolationForestAnomalyDetector,
    training_range: str,
    model_id: str = "isolation_forest",
) -> dict[str, Any]:
    """Validate, checksum, and atomically promote one tenant artifact.

    Artifacts are never overwritten.  ``active.json`` is the only pointer and
    is replaced atomically; ``previous.json`` remains available for rollback.
    """
    if detector.model is None:
        raise ValueError("cannot promote an untrained detector")
    if getattr(detector.model, "n_features_in_", len(FEATURE_COLUMNS)) != len(
        FEATURE_COLUMNS
    ):
        raise ValueError("model feature dimension does not match feature schema")
    detector.model.decision_function([[0.0] * len(FEATURE_COLUMNS)])

    root = Path(base_path).parent / "users" / tenant_hash(tenant_id, owner_user_id)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".promotion.lock"
    with PromotionLock(lock_path):
        existing_active = root / "active.json"
        previous = root / "previous.json"
        if existing_active.exists():
            os.replace(existing_active, previous)

        version = time.time_ns()
        artifact_path = root / f"{model_id}.v{version}.joblib"
        temp_path = root / f".{model_id}.v{version}.tmp"
        metadata = {
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "model_id": model_id,
            "version": version,
            "training_range": training_range,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "artifact_uri": str(artifact_path),
            "status": "candidate",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        detector.save_model(temp_path, artifact_metadata=metadata)
        checksum = _checksum(temp_path)
        loaded = IsolationForestAnomalyDetector.load_model(temp_path)
        if loaded.model is None or getattr(loaded.model, "n_features_in_", 0) != len(
            FEATURE_COLUMNS
        ):
            temp_path.unlink(missing_ok=True)
            raise ValueError("candidate model failed validation")
        metadata.update({"checksum": checksum, "status": "active"})
        store = configured_artifact_store(base_path)
        key = f"models/{tenant_hash(tenant_id, owner_user_id)}/{model_id}.v{version}.joblib"
        if isinstance(store, LocalModelArtifactStore):
            os.replace(temp_path, artifact_path)
            artifact_uri = str(artifact_path.resolve())
        else:
            artifact_uri = store.put_immutable(key, temp_path.read_bytes())
            temp_path.unlink(missing_ok=True)
        metadata["artifact_uri"] = artifact_uri
        pointer_tmp = root / ".active.json.tmp"
        pointer_tmp.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        os.replace(pointer_tmp, root / "active.json")
        return metadata


def load_active_detector(
    base_path: str | Path, tenant_id: str, owner_user_id: int
) -> IsolationForestAnomalyDetector | None:
    root = Path(base_path).parent / "users" / tenant_hash(tenant_id, owner_user_id)
    pointer = root / "active.json"
    if not pointer.exists():
        return None
    metadata = json.loads(pointer.read_text(encoding="utf-8"))
    artifact = Path(metadata["artifact_uri"])
    if not artifact.is_absolute():
        artifact = root / artifact
    if (
        metadata.get("tenant_id") != tenant_id
        or metadata.get("owner_user_id") != owner_user_id
        or _checksum(artifact) != metadata.get("checksum")
    ):
        raise ValueError("active user model checksum or scope identity mismatch")
    detector = IsolationForestAnomalyDetector.load_model(artifact)
    if detector.model is None or getattr(detector.model, "n_features_in_", 0) != len(
        FEATURE_COLUMNS
    ):
        raise ValueError("active tenant model feature schema mismatch")
    return detector


async def load_registered_detector(
    base_path: str | Path, tenant_id: str, owner_user_id: int
) -> tuple[str, IsolationForestAnomalyDetector] | None:
    """Load the database-authoritative active version through the configured store."""
    async with get_engine().connect() as conn:
        result = await conn.execute(
            text("""
                SELECT version, artifact_uri, checksum, feature_schema_version
                FROM model_artifacts
                WHERE tenant_id = :tenant_id AND owner_user_id = :owner_user_id
                  AND model_id = 'isolation_forest' AND status = 'active'
                LIMIT 1
            """),
            {"tenant_id": tenant_id, "owner_user_id": owner_user_id},
        )
        row = result.mappings().first()
    if row is None:
        return None
    if row["feature_schema_version"] != FEATURE_SCHEMA_VERSION:
        raise ValueError("active tenant model feature schema mismatch")
    store = configured_artifact_store(base_path)
    data = await run_storage_io(store.get, row["artifact_uri"])
    checksum = hashlib.sha256(data).hexdigest()
    if checksum != row["checksum"]:
        raise ValueError("active tenant model checksum mismatch")
    cache = Path(base_path).parent / "cache" / tenant_hash(tenant_id, owner_user_id)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"isolation_forest.v{row['version']}.joblib"
    if not target.exists():
        temp = target.with_suffix(".tmp")
        temp.write_bytes(data)
        os.replace(temp, target)
    detector = IsolationForestAnomalyDetector.load_model(target)
    if detector.model is None or getattr(detector.model, "n_features_in_", 0) != len(
        FEATURE_COLUMNS
    ):
        raise ValueError("active tenant model failed runtime validation")
    return str(row["version"]), detector


async def promote_registered_version(
    tenant_id: str, owner_user_id: int, version: int
) -> None:
    """Atomically repoint a tenant to any retained, checksum-valid version."""
    async with get_engine().begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id))"),
            {"tenant_id": tenant_id},
        )
        candidate = (
            (
                await conn.execute(
                    text(
                        "SELECT artifact_uri, checksum FROM model_artifacts WHERE tenant_id=:tenant_id AND owner_user_id=:owner_user_id AND model_id='isolation_forest' AND version=:version FOR UPDATE"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "owner_user_id": owner_user_id,
                        "version": version,
                    },
                )
            )
            .mappings()
            .first()
        )
        if candidate is None:
            raise ValueError("requested model version does not exist")
        store = configured_artifact_store("models/isolation_forest.joblib")
        data = await run_storage_io(store.get, candidate["artifact_uri"])
        if hashlib.sha256(data).hexdigest() != candidate["checksum"]:
            raise ValueError("requested model artifact checksum mismatch")
        await conn.execute(
            text(
                "UPDATE model_artifacts SET status='previous' WHERE tenant_id=:tenant_id AND owner_user_id=:owner_user_id AND model_id='isolation_forest' AND status='active'"
            ),
            {"tenant_id": tenant_id, "owner_user_id": owner_user_id},
        )
        await conn.execute(
            text(
                "UPDATE model_artifacts SET status='active', promoted_at=NOW() WHERE tenant_id=:tenant_id AND owner_user_id=:owner_user_id AND model_id='isolation_forest' AND version=:version"
            ),
            {
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "version": version,
            },
        )

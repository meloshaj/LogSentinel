#!/usr/bin/env python3
"""Record a verified OCI production release without deploying it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from release_manifest import validate_manifest
except ModuleNotFoundError:
    from scripts.release_manifest import validate_manifest


IMAGE_ROLES = ("backend", "worker", "caddy")
REQUIRED_SERVICES = (
    "backend",
    "pipeline",
    "webhook",
    "archive",
    "caddy",
    "frontend",
    "postgres",
    "valkey",
    "prometheus",
    "alertmanager",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class MarkerError(ValueError):
    """Release evidence does not support a production marker update."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarkerError(f"evidence-unavailable:{path.name}") from exc
    if not isinstance(value, dict):
        raise MarkerError(f"evidence-invalid:{path.name}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise MarkerError("marker-path-must-not-be-a-symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _checks_pass(verification: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    checks = verification.get("checks", {})
    if verification.get("status") != "PASS":
        errors.append("production-verifier-status")
    for name in (
        "source",
        "migration",
        "sbom",
        "scan",
        "host_only_retention",
        "host_release_tooling",
        "release_gate",
    ):
        if checks.get(name, {}).get("match") is not True:
            errors.append(f"production-verifier.{name}")
    images = checks.get("images", {})
    for role in IMAGE_ROLES:
        if images.get(role, {}).get("match") is not True:
            errors.append(f"production-verifier.image.{role}")
    return errors


def _health_errors(
    health: dict[str, Any], expected_migration: str, release_id: str
) -> list[str]:
    errors: list[str] = []
    if health.get("status") != "PASS":
        errors.append("production-health-status")
    if health.get("release_id") != release_id:
        errors.append("production-health-release-id")
    deployment_time = health.get("deployment_timestamp_utc")
    if not isinstance(deployment_time, str):
        errors.append("deployment-timestamp-missing")
    else:
        try:
            parsed_deployment_time = datetime.fromisoformat(
                deployment_time.replace("Z", "+00:00")
            )
            if parsed_deployment_time.tzinfo is None:
                errors.append("deployment-timestamp-timezone-missing")
        except ValueError:
            errors.append("deployment-timestamp-invalid")
    stability = health.get("stability", {})
    if stability.get("duration_seconds", 0) < 120 or stability.get("samples", 0) < 3:
        errors.append("production-stability-window")
    if health.get("readiness") != "READY":
        errors.append("backend-readiness")
    services = health.get("services", {})
    for name in REQUIRED_SERVICES:
        if services.get(name) != "HEALTHY":
            errors.append(f"production-service.{name}")
    heartbeats = health.get("worker_heartbeats", {})
    for role in ("pipeline", "webhook", "archive"):
        if heartbeats.get(role) != "HEALTHY":
            errors.append(f"worker-heartbeat.{role}")
    stream = health.get("stream", {})
    if stream.get("pending") != 0 or stream.get("lag") != 0:
        errors.append("production-stream-pending-or-lag")
    dlq = health.get("dlq", {})
    if dlq.get("count") != 200 or dlq.get("modified") is not False:
        errors.append("known-dlq-quarantine")
    retention = health.get("retention", {})
    if (
        retention.get("approval") != "PENDING"
        or retention.get("apply_enabled") is not False
    ):
        errors.append("retention-policy-boundary")
    if health.get("backup_pitr", {}).get("status") != "HEALTHY":
        errors.append("backup-pitr-health")
    if health.get("backup_timer", {}).get("status") != "ACTIVE":
        errors.append("backup-timer")
    if health.get("migration_head") != expected_migration:
        errors.append("production-health-migration")
    return errors


def record_release(
    *,
    manifest_path: Path,
    gate_path: Path,
    verification_path: Path,
    health_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve(strict=True)
    gate_path = gate_path.resolve(strict=True)
    verification_path = verification_path.resolve(strict=True)
    health_path = health_path.resolve(strict=True)
    manifest = _read_json(manifest_path)
    errors = validate_manifest(manifest, require_release_ready=True)
    if errors:
        raise MarkerError("release-manifest-invalid")
    release_id = manifest.get("release_id")
    if not isinstance(release_id, str):
        raise MarkerError("release-manifest-id-missing")
    source_sha = manifest.get("source", {}).get("runtime_manifest_sha256")
    artifact_sha = manifest.get("artifacts", {}).get("artifact_sha256")
    gate = _read_json(gate_path)
    verification = _read_json(verification_path)
    health = _read_json(health_path)
    if (
        gate.get("status") != "PASS"
        or gate.get("release_id") != release_id
        or gate.get("source_manifest_sha256") != source_sha
        or gate.get("details", {}).get("artifact_sha256") != artifact_sha
        or gate.get("details", {}).get("oci_image_archive_sha256")
        != manifest.get("artifacts", {}).get("oci_image_archive_sha256")
        or gate.get("details", {}).get("eligible_for_explicit_deployment") is not True
        or gate.get("details", {}).get("deployment_authorized") is not False
    ):
        errors.append("release-gate-result")
    if verification.get("release_id") != release_id:
        errors.append("production-verification-release-id")
    errors.extend(_checks_pass(verification))
    expected_head_value = manifest.get("migration", {}).get("head")
    if not isinstance(expected_head_value, str):
        errors.append("release-manifest-migration-head")
    expected_head = expected_head_value if isinstance(expected_head_value, str) else ""
    errors.extend(_health_errors(health, expected_head, release_id))
    if errors:
        raise MarkerError("production-release-evidence-incomplete")

    images = {role: manifest["images"][role]["identity"] for role in IMAGE_ROLES}
    if any(not IMAGE_ID_RE.fullmatch(identity) for identity in images.values()):
        raise MarkerError("production-image-identity-invalid")
    marker = {
        "schema_version": "1.0",
        "status": "PASS",
        "release_id": release_id,
        "source_manifest_sha256": source_sha,
        "artifact_sha256": artifact_sha,
        "oci_image_archive_sha256": manifest.get("artifacts", {}).get(
            "oci_image_archive_sha256"
        ),
        "image_ids": images,
        "migration_head": expected_head,
        "deployment_timestamp_utc": health["deployment_timestamp_utc"],
        "verification_completed_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "release_manifest_sha256": _sha256_file(manifest_path),
        "release_gate_evidence_sha256": _sha256_file(gate_path),
        "production_verification_sha256": _sha256_file(verification_path),
        "production_health_sha256": _sha256_file(health_path),
        "rollback": manifest.get("rollback", {}),
        "deployment_authorized_by_gate": False,
        "verification_completed": True,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink():
        raise MarkerError("marker-output-directory-must-not-be-a-symlink")
    current_path = output_dir / "current-release.json"
    previous_path = output_dir / "previous-known-good.json"
    if current_path.is_symlink() or previous_path.is_symlink():
        raise MarkerError("release-marker-must-not-be-a-symlink")
    if current_path.is_file():
        previous = _read_json(current_path)
        if previous.get("status") != "PASS" or not previous.get("release_id"):
            raise MarkerError("existing-current-marker-is-not-known-good")
        if previous.get("release_id") != release_id:
            _write_json_atomic(previous_path, previous)
    _write_json_atomic(current_path, marker)
    return {
        "status": "PASS",
        "release_id": release_id,
        "current_marker": str(current_path),
        "previous_marker": str(previous_path) if previous_path.is_file() else None,
        "deployment_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--release-gate-result", required=True)
    parser.add_argument("--production-verification", required=True)
    parser.add_argument("--production-health", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = record_release(
            manifest_path=Path(args.manifest),
            gate_path=Path(args.release_gate_result),
            verification_path=Path(args.production_verification),
            health_path=Path(args.production_health),
            output_dir=Path(args.output_dir),
        )
    except (MarkerError, OSError, ValueError):
        print(
            json.dumps({"status": "UNKNOWN", "reason": "release-evidence-incomplete"})
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

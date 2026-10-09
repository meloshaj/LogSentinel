from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.record_production_release import MarkerError, record_release
from scripts.release_manifest import (
    REQUIRED_RELEASE_GATES,
    host_operations_manifest,
)


def _write(path: Path, value: dict) -> str:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    release_id = "manual-marker-test"
    images = {
        name: {"identity": f"sha256:{index:064x}", "immutable": True}
        for index, name in enumerate(("backend", "worker", "caddy"), start=1)
    }
    sboms = {}
    scans = {}
    for index, name in enumerate(("backend", "worker", "caddy"), start=1):
        sbom_path = tmp_path / f"{name}.spdx.json"
        sbom_sha = _write(sbom_path, {"spdxVersion": "SPDX-2.3"})
        scan_path = tmp_path / f"{name}.trivy.json"
        scan_sha = _write(scan_path, {"Results": []})
        sboms[name] = {
            "artifact": sbom_path.name,
            "sha256": sbom_sha,
            "image_identity": images[name]["identity"],
        }
        scans[name] = {
            "artifact": scan_path.name,
            "image_id": images[name]["identity"],
            "high": 0,
            "critical": 0,
            "ignore_unfixed": False,
            "exceptions": False,
            "sha256": scan_sha,
            "sbom_sha256": sbom_sha,
        }

    source_sha = "a" * 64
    artifact_sha = "b" * 64
    image_archive_sha = "c" * 64
    gate_path = tmp_path / "release-gate-result.json"
    gate_sha = _write(
        gate_path,
        {
            "status": "PASS",
            "release_id": release_id,
            "source_manifest_sha256": source_sha,
            "details": {
                "artifact_sha256": artifact_sha,
                "oci_image_archive_sha256": image_archive_sha,
                "eligible_for_explicit_deployment": True,
                "deployment_authorized": False,
            },
        },
    )
    previous = {
        name: {"image_id": f"sha256:{index + 10:064x}"}
        for index, name in enumerate(("backend", "worker", "caddy"), start=1)
    }
    manifest = {
        "schema_version": "1.0",
        "release_id": release_id,
        "source": {"runtime_manifest_sha256": source_sha},
        "host_operations": host_operations_manifest(),
        "artifacts": {
            "artifact_sha256": artifact_sha,
            "oci_image_archive_sha256": image_archive_sha,
            "oci_image_archive_path": "oci-images.tar",
        },
        "images": images,
        "migration": {
            "head": "20260917_0011_password_reset_atomicity",
            "required_head": "20260917_0011_password_reset_atomicity",
        },
        "sbom": {"artifacts": sboms},
        "security_scan": {"status": "PASS", "exceptions": [], "images": scans},
        "gates": {name: "PASS" for name in REQUIRED_RELEASE_GATES},
        "provenance": {
            "classification": "HISTORICAL_BUILD_OUTPUT",
            "signature_enforced": False,
            "attestation_state": "UNSIGNED",
        },
        "rollback": {
            "status": "PASS",
            "previous": previous,
            "current_migration_head": "20260917_0011_password_reset_atomicity",
            "migration_compatible": True,
            "source_identity": "previous image and source identity",
            "config_sha256": "d" * 64,
            "old_images_deleted": False,
        },
        "release_gate_result": {
            "path": gate_path.name,
            "sha256": gate_sha,
        },
    }
    manifest_path = tmp_path / "release-manifest.json"
    _write(manifest_path, manifest)

    checks = {
        name: {"match": True}
        for name in (
            "source",
            "migration",
            "sbom",
            "scan",
            "host_only_retention",
            "host_release_tooling",
            "release_gate",
        )
    }
    checks["images"] = {name: {"match": True} for name in images}
    verification_path = tmp_path / "production-verification.json"
    _write(
        verification_path,
        {"status": "PASS", "release_id": release_id, "checks": checks},
    )

    health_path = tmp_path / "production-health.json"
    _write(
        health_path,
        {
            "status": "PASS",
            "release_id": release_id,
            "deployment_timestamp_utc": "2026-09-21T05:00:00Z",
            "readiness": "READY",
            "stability": {"duration_seconds": 120, "samples": 3},
            "services": {
                name: "HEALTHY"
                for name in (
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
            },
            "worker_heartbeats": {
                "pipeline": "HEALTHY",
                "webhook": "HEALTHY",
                "archive": "HEALTHY",
            },
            "stream": {"pending": 0, "lag": 0},
            "dlq": {"count": 200, "modified": False},
            "retention": {"approval": "PENDING", "apply_enabled": False},
            "backup_pitr": {"status": "HEALTHY"},
            "backup_timer": {"status": "ACTIVE"},
            "migration_head": "20260917_0011_password_reset_atomicity",
        },
    )
    return manifest_path, gate_path, verification_path, health_path


def test_marker_records_verified_release_and_preserves_previous_pointer(
    tmp_path: Path,
) -> None:
    manifest, gate, verification, health = _evidence(tmp_path)
    output = tmp_path / "production-metadata"
    output.mkdir()
    old = {"status": "PASS", "release_id": "previous-known-good"}
    (output / "current-release.json").write_text(json.dumps(old), encoding="utf-8")

    result = record_release(
        manifest_path=manifest,
        gate_path=gate,
        verification_path=verification,
        health_path=health,
        output_dir=output,
    )

    current = json.loads((output / "current-release.json").read_text(encoding="utf-8"))
    previous = json.loads(
        (output / "previous-known-good.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "PASS"
    assert result["deployment_performed"] is False
    assert current["release_id"] == "manual-marker-test"
    assert current["deployment_authorized_by_gate"] is False
    assert previous == old


def test_marker_refuses_short_stability_window(tmp_path: Path) -> None:
    manifest, gate, verification, health = _evidence(tmp_path)
    value = json.loads(health.read_text(encoding="utf-8"))
    value["stability"]["duration_seconds"] = 30
    _write(health, value)
    output = tmp_path / "production-metadata"

    with pytest.raises(MarkerError, match="production-release-evidence-incomplete"):
        record_release(
            manifest_path=manifest,
            gate_path=gate,
            verification_path=verification,
            health_path=health,
            output_dir=output,
        )
    assert not output.joinpath("current-release.json").exists()

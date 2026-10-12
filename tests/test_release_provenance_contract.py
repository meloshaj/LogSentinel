from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from scripts.release_attestation import build_attestation
from scripts.release_manifest import (
    REQUIRED_RELEASE_GATES,
    HOST_RELEASE_FILES,
    build_manifest,
    host_operations_manifest,
    runtime_manifest,
    validate_manifest,
)
from scripts.verify_release import verify


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_manifest_is_deterministic_and_secret_free() -> None:
    first = runtime_manifest(ROOT)
    second = runtime_manifest(ROOT)
    assert first["sha256"] == second["sha256"]
    assert all(".env" not in item["path"] for item in first["files"])
    assert all("temporary-report" not in item["path"] for item in first["files"])
    assert not set(item["path"] for item in first["files"]) & HOST_RELEASE_FILES


def test_host_only_operational_files_do_not_change_runtime_manifest() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "scripts").mkdir()
        (root / "scripts" / "app_runtime.py").write_text(
            "runtime = True\n", encoding="utf-8"
        )
        (root / "scripts" / "retention.py").write_text(
            "plan_only = True\n", encoding="utf-8"
        )
        before = runtime_manifest(root)
        (root / "scripts" / "retention.py").write_text(
            "plan_only = True\nchanged = True\n", encoding="utf-8"
        )
        after = runtime_manifest(root)
        assert before == after
        assert [item["path"] for item in before["files"]] == ["scripts/app_runtime.py"]


def test_release_manifest_requires_immutable_identity_for_release_ready() -> None:
    class Args:
        release_id = "test-release"
        source_commit = "abcdef1234567890"
        artifact = None
        image = ["backend=mutable:test"]
        sbom = []
        migration_head = "20260917_0011_password_reset_atomicity"
        scan_status = "PASS"
        gate = ["quality=PASS"]
        builder = "test"
        build_timestamp = "2026-09-18T00:00:00Z"
        bootstrapped = False

    manifest = build_manifest(Args())
    assert validate_manifest(manifest) == []
    assert "images.immutable_identity_required" in validate_manifest(
        manifest, require_release_ready=True
    )


def test_missing_evidence_and_scanner_failure_fail_release_ready() -> None:
    class Args:
        release_id = "test-release"
        source_commit = "abcdef1234567890"
        artifact = None
        image = ["backend=sha256:" + "d" * 64]
        sbom = []
        migration_head = "20260917_0011_password_reset_atomicity"
        scan_status = "SCANNER FAILURE"
        gate = ["release-gate=PASS"]
        builder = "test"
        build_timestamp = "2026-09-18T00:00:00Z"
        bootstrapped = False

    manifest = build_manifest(Args())
    errors = validate_manifest(manifest, require_release_ready=True)
    assert "sbom" in errors
    assert "security_scan" in errors


def test_sbom_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    sbom_path = tmp_path / "backend.spdx.json"
    sbom_path.write_text("{}\n", encoding="utf-8")

    class Args:
        release_id = "test-release"
        source_commit = "abcdef1234567890"
        artifact = None
        image = ["backend=sha256:" + "d" * 64]
        sbom = [f"backend={sbom_path}"]
        migration_head = "20260917_0011_password_reset_atomicity"
        scan_status = "PASS"
        gate = ["release-gate=PASS"]
        builder = "test"
        build_timestamp = "2026-09-18T00:00:00Z"
        bootstrapped = False

    manifest = build_manifest(Args())
    manifest["sbom"]["artifacts"]["backend"]["sha256"] = "not-a-sha256"
    assert "sbom.backend.sha256" in validate_manifest(manifest)


def test_verify_fails_closed_for_source_image_and_migration_mismatches(
    tmp_path: Path,
) -> None:
    release_id = "test-release"
    expected_images = {
        name: f"sha256:{index:064x}"
        for index, name in enumerate(("backend", "worker", "caddy"), start=1)
    }
    sbom_artifacts = {}
    security_images = {}
    image_inputs = []
    for index, name in enumerate(("backend", "worker", "caddy"), start=1):
        sbom_path = tmp_path / f"{name}.spdx.json"
        sbom_path.write_text("{}\n", encoding="utf-8")
        sbom_sha = hashlib.sha256(sbom_path.read_bytes()).hexdigest()
        scan_path = tmp_path / f"{name}.trivy.json"
        scan_path.write_text('{"Results":[]}\n', encoding="utf-8")
        scan_sha = hashlib.sha256(scan_path.read_bytes()).hexdigest()
        sbom_artifacts[name] = {
            "artifact": sbom_path.name,
            "format": "SPDX-JSON",
            "sha256": sbom_sha,
            "image_identity": expected_images[name],
        }
        security_images[name] = {
            "artifact": scan_path.name,
            "image_id": expected_images[name],
            "high": 0,
            "critical": 0,
            "ignore_unfixed": False,
            "exceptions": False,
            "sha256": scan_sha,
            "sbom_sha256": sbom_sha,
        }
        image_inputs.append(f"{name}={expected_images[name]}")

    artifact_path = tmp_path / "runtime-source.tar"
    artifact_path.write_bytes(b"release")
    artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    source_sha = runtime_manifest(ROOT)["sha256"]
    gate_path = tmp_path / "release-gate-result.json"
    gate_payload = {
        "status": "PASS",
        "release_id": release_id,
        "source_manifest_sha256": source_sha,
        "details": {
            "artifact_sha256": artifact_sha,
            "oci_image_archive_sha256": hashlib.sha256(b"images").hexdigest(),
            "eligible_for_explicit_deployment": True,
        },
    }
    gate_path.write_text(json.dumps(gate_payload), encoding="utf-8")
    gate_sha = hashlib.sha256(gate_path.read_bytes()).hexdigest()
    previous = {
        name: {"image_id": f"sha256:{index + 10:064x}"}
        for index, name in enumerate(("backend", "worker", "caddy"), start=1)
    }
    manifest = {
        "schema_version": "1.0",
        "release_id": release_id,
        "source": {"runtime_manifest_sha256": source_sha},
        "host_operations": host_operations_manifest(ROOT),
        "artifacts": {
            "artifact_sha256": artifact_sha,
            "oci_image_archive_sha256": hashlib.sha256(b"images").hexdigest(),
            "oci_image_archive_path": "oci-images.tar",
        },
        "images": {
            name: {"identity": identity, "immutable": True}
            for name, identity in expected_images.items()
        },
        "migration": {
            "head": "20260917_0011_password_reset_atomicity",
            "required_head": "20260917_0011_password_reset_atomicity",
        },
        "sbom": {"artifacts": sbom_artifacts},
        "security_scan": {
            "status": "PASS",
            "exceptions": [],
            "images": security_images,
        },
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
            "source_identity": "previous release identity",
            "config_sha256": "f" * 64,
            "old_images_deleted": False,
        },
        "release_gate_result": {"path": gate_path.name, "sha256": gate_sha},
    }
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    base = dict(
        manifest=str(manifest_path),
        evidence_root=str(tmp_path),
        source_root=str(ROOT),
        image=[*image_inputs],
        backend_container=None,
        frontend_container=None,
        migration_head="20260917_0011_password_reset_atomicity",
        database_url=None,
    )
    assert validate_manifest(manifest, require_release_ready=True) == []
    assert asyncio.run(verify(SimpleNamespace(**base)))["status"] == "PASS"
    source_mismatch = dict(base, source_root=str(tmp_path / "empty"))
    assert asyncio.run(verify(SimpleNamespace(**source_mismatch)))["status"] == "FAIL"
    image_mismatch = dict(base, image=["backend=sha256:" + "e" * 64, *image_inputs[1:]])
    assert asyncio.run(verify(SimpleNamespace(**image_mismatch)))["status"] == "FAIL"
    migration_mismatch = dict(
        base, migration_head="20260913_0010_per_user_data_ownership"
    )
    assert (
        asyncio.run(verify(SimpleNamespace(**migration_mismatch)))["status"] == "FAIL"
    )


def test_attestation_is_recognized_metadata_without_claiming_signature() -> None:
    manifest = {
        "build": {"builder": "test", "timestamp_utc": "2026-09-18T00:00:00Z"},
        "source": {"commit": "abcdef1234567890", "runtime_manifest_sha256": "a" * 64},
        "migration": {"head": "20260917_0011_password_reset_atomicity"},
        "gates": {"release-gate": "PASS"},
        "sbom": {"artifacts": {"backend": {"sha256": "b" * 64}}},
        "artifacts": {"artifact_sha256": "c" * 64},
        "images": {"backend": {"identity": "sha256:" + "d" * 64}},
    }
    attestation = build_attestation(manifest)
    assert attestation["predicateType"].startswith("https://slsa.dev/")
    assert attestation["signature_status"] == "NOT_APPROVED"


def test_unsigned_state_and_rollback_metadata_are_explicit() -> None:
    class Args:
        release_id = "test-release"
        source_commit = "abcdef1234567890"
        artifact = None
        image = ["backend=sha256:" + "d" * 64]
        sbom = []
        migration_head = "20260917_0011_password_reset_atomicity"
        scan_status = "NOT_RUN"
        gate = []
        builder = "test"
        build_timestamp = "2026-09-18T00:00:00Z"
        bootstrapped = True

    manifest = build_manifest(Args())
    assert manifest["provenance"]["attestation_state"] == "UNSIGNED"
    assert manifest["provenance"]["signature_enforced"] is False
    assert manifest["rollback"]["status"] == "NOT_RUN"

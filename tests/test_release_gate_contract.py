from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.release_gate import (
    GateError,
    IMAGE_NAMES,
    REQUIRED_CONTROLS,
    _make_tar,
    _manifest_parts,
    _phase_result,
    _require_source_review,
    _validate_rollback,
    _verify_source_archive,
    release_ready,
    write_json,
)
from scripts.release_manifest import host_operations_manifest, validate_manifest


def _manifest(path: Path, *, source_path: str = "src/app.ts") -> None:
    files = [{"path": source_path, "sha256": "a" * 64}]
    canonical = json.dumps(files, separators=(",", ":"), sort_keys=True).encode()
    path.write_text(
        json.dumps(
            {
                "file_count": 1,
                "files": files,
                "sha256": hashlib.sha256(canonical).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def test_source_manifest_digest_and_order_are_checked(tmp_path: Path) -> None:
    manifest = tmp_path / "source.json"
    _manifest(manifest)
    _, entries = _manifest_parts(manifest)
    assert entries == [{"path": "src/app.ts", "sha256": "a" * 64}]


def test_source_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    manifest = tmp_path / "source.json"
    _manifest(manifest, source_path="../outside")
    with pytest.raises(GateError, match="unsafe path"):
        _manifest_parts(manifest)


def test_source_review_binds_runtime_and_host_tool_digests() -> None:
    host_operations = host_operations_manifest()
    payload = {
        "host_operations": host_operations,
        "review": {
            "status": "PASS",
            "runtime_manifest_sha256": "a" * 64,
            "host_operations_sha256": host_operations["sha256"],
        },
    }
    _require_source_review(payload, "a" * 64)
    payload["review"]["host_operations_sha256"] = "b" * 64
    with pytest.raises(GateError, match="not explicitly reviewed"):
        _require_source_review(payload, "a" * 64)


def test_rollback_requires_immutable_old_images_and_compatibility() -> None:
    previous = {
        "backend": {"image_id": "sha256:" + "a" * 64},
        "workers": {"image_id": "sha256:" + "b" * 64},
        "caddy": {"image_id": "sha256:" + "c" * 64},
    }
    contract = {
        "status": "RECORDED",
        "previous": previous,
        "current_migration_head": "20260917_0011_password_reset_atomicity",
        "migration_compatible": True,
        "source_identity": "previous image identities",
        "config_sha256": "d" * 64,
        "old_images_deleted": False,
    }
    assert _validate_rollback(contract, "20260917_0011_password_reset_atomicity") == []
    contract["migration_compatible"] = False
    assert "rollback.migration_compatible" in _validate_rollback(
        contract, "20260917_0011_password_reset_atomicity"
    )


def test_release_ready_unknown_on_missing_phase_and_never_authorizes_deploy(
    tmp_path: Path,
) -> None:
    result = release_ready("manual-20260921", tmp_path)
    assert result["status"] == "UNKNOWN"
    assert result["details"]["eligible_for_explicit_deployment"] is False
    assert result["details"]["deployment_authorized"] is False


def test_release_ready_blocks_phase_source_mismatch(tmp_path: Path) -> None:
    release_id = "manual-20260921"
    source_sha = "a" * 64
    for phase in ("validate", "build", "security", "package", "verify"):
        record = _phase_result(
            release_id=release_id,
            phase=phase,
            status="PASS",
            source_sha="b" * 64 if phase == "verify" else source_sha,
        )
        if phase == "validate":
            record["controls"] = {
                name: {"status": "PASS"}
                for name in (
                    "python_quality",
                    "backend_tests",
                    "root_tests",
                    "frontend_tests",
                    "frontend_typecheck",
                    "frontend_build",
                    "browser_truth",
                    "migration_contract",
                    "backup_dr_contract",
                    "retention_contract",
                    "production_compose",
                    "monitoring_compose",
                    "workflow_validation",
                    "rollback_contract",
                )
            }
        write_json(tmp_path / release_id / f"{phase}.json", record)
    result = release_ready(release_id, tmp_path)
    assert result["status"] == "FAIL"
    assert "source-manifest-changed-between-phases" in result["details"]["reasons"]


def test_release_manifest_requires_complete_scan_sbom_and_rollback_contract() -> None:
    images = {
        name: {"identity": f"sha256:{index:064x}", "immutable": True}
        for index, name in enumerate(IMAGE_NAMES, start=1)
    }
    sboms = {
        name: {
            "artifact": f"security/{name}.spdx.json",
            "sha256": f"{index + 10:064x}",
            "image_identity": images[name]["identity"],
        }
        for index, name in enumerate(IMAGE_NAMES, start=1)
    }
    scans = {
        name: {
            "image_id": images[name]["identity"],
            "high": 0,
            "critical": 0,
            "ignore_unfixed": False,
            "exceptions": False,
            "sha256": f"{index + 20:064x}",
            "sbom_sha256": sboms[name]["sha256"],
            "artifact": f"security/{name}.trivy.json",
        }
        for index, name in enumerate(IMAGE_NAMES, start=1)
    }
    previous = {
        name: {"image_id": f"sha256:{index + 30:064x}"}
        for index, name in enumerate(IMAGE_NAMES, start=1)
    }
    manifest = {
        "schema_version": "1.0",
        "release_id": "manual-20260921",
        "source": {"runtime_manifest_sha256": "a" * 64},
        "host_operations": host_operations_manifest(),
        "artifacts": {
            "artifact_sha256": "b" * 64,
            "oci_image_archive_sha256": "d" * 64,
            "oci_image_archive_path": "artifact/oci-images.tar",
        },
        "images": images,
        "migration": {
            "head": "20260917_0011_password_reset_atomicity",
            "required_head": "20260917_0011_password_reset_atomicity",
        },
        "sbom": {"artifacts": sboms},
        "security_scan": {"status": "PASS", "exceptions": [], "images": scans},
        "gates": {name: "PASS" for name in REQUIRED_CONTROLS},
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
            "source_identity": "prior release source and image identities",
            "config_sha256": "c" * 64,
            "old_images_deleted": False,
        },
    }
    assert validate_manifest(manifest, require_release_ready=True) == []
    manifest["security_scan"]["images"]["worker"]["critical"] = 1
    assert "security_scan.worker.policy-or-linkage" in validate_manifest(
        manifest, require_release_ready=True
    )


def test_staged_source_must_match_archive_manifest_hashes(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "app.py").write_text("print('release')\n", encoding="utf-8")
    digest = hashlib.sha256((source_root / "app.py").read_bytes()).hexdigest()
    files = [{"path": "app.py", "sha256": digest}]
    canonical = json.dumps(files, separators=(",", ":"), sort_keys=True).encode()
    source_manifest = {
        "host_operations": host_operations_manifest(source_root),
        "review": {
            "status": "PASS",
            "runtime_manifest_sha256": hashlib.sha256(canonical).hexdigest(),
            "host_operations_sha256": host_operations_manifest(source_root)["sha256"],
        },
        "runtime_manifest": {
            "file_count": 1,
            "files": files,
            "sha256": hashlib.sha256(canonical).hexdigest(),
        },
    }
    archive = tmp_path / "runtime-source.tar"
    _make_tar(
        source_root,
        files,
        source_manifest,
        archive,
    )
    assert (
        _verify_source_archive(
            archive, source_root, source_manifest["runtime_manifest"]["sha256"]
        )
        == []
    )
    (source_root / "app.py").write_text("print('changed')\n", encoding="utf-8")
    errors = _verify_source_archive(
        archive, source_root, source_manifest["runtime_manifest"]["sha256"]
    )
    assert "staged-source:hash-mismatch:app.py" in errors

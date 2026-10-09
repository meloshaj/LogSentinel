#!/usr/bin/env python3
"""Create and validate a secret-free deterministic release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATHS = (
    "backend/app",
    "backend/requirements.txt",
    "scripts",
    "src",
    "public",
    "config",
    # Inputs consumed by the frontend container build. Keeping lockfiles and
    # compiler configuration in the runtime manifest makes the built assets
    # reproducible from the packaged source rather than from an ambient tree.
    "package.json",
    "package-lock.json",
    "index.html",
    "vite.config.ts",
    "tsconfig.json",
    "postcss.config.mjs",
    ".dockerignore",
    "Dockerfile",
    "backend/Dockerfile",
    "docker/postgres/Dockerfile",
    "docker-compose.prod.yml",
    "deploy/caddy/Caddyfile",
    "deploy/monitoring",
    "deploy/helm/logsentinel",
)
EXCLUDED_PARTS = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "test-results",
    "dist",
    "build",
    "logs",
    "temporary-report",
}
SECRET_NAMES = {".env", ".env.production", ".env.local"}
REQUIRED_RELEASE_GATES = (
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
CI_ONLY_FILES = {
    "config/release-manifest.schema.json",
    "scripts/release_attestation.py",
    "scripts/release_gate.py",
    "scripts/release_manifest.py",
    "scripts/validate_ci_workflow.py",
    "scripts/verify_release.py",
}
HOST_OPERATIONAL_FILES = {
    "config/retention-policy.yml",
    "scripts/backup_status.py",
    "scripts/local_retention.py",
    "scripts/recovery_retention.py",
    "scripts/retention.py",
}
HOST_RELEASE_FILES = {
    "scripts/release_gate.py",
    "scripts/release_manifest.py",
    "scripts/verify_release.py",
    "scripts/record_production_release.py",
}
NON_RUNTIME_PREFIXES = (
    "scripts/integration/",
    "scripts/benchmarks/",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_files(root: Path = ROOT) -> list[Path]:
    paths: list[Path] = []
    for entry in RUNTIME_PATHS:
        candidate = root / entry
        if candidate.is_file():
            paths.append(candidate)
        elif candidate.is_dir():
            paths.extend(
                path
                for path in candidate.rglob("*")
                if path.is_file()
                and not any(part in EXCLUDED_PARTS for part in path.parts)
                and path.name not in SECRET_NAMES
                and not path.name.endswith((".pyc", ".pem", ".key"))
                and _is_runtime_path(path.relative_to(root))
            )
    return sorted(set(paths), key=lambda path: path.relative_to(root).as_posix())


def _is_runtime_path(relative: Path) -> bool:
    value = relative.as_posix()
    return (
        value not in CI_ONLY_FILES
        and value not in HOST_OPERATIONAL_FILES
        and value not in HOST_RELEASE_FILES
        and not any(value.startswith(prefix) for prefix in NON_RUNTIME_PREFIXES)
    )


def runtime_manifest(root: Path = ROOT) -> dict[str, Any]:
    files = [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
        for path in runtime_files(root)
    ]
    canonical = json.dumps(files, separators=(",", ":"), sort_keys=True).encode()
    return {
        "file_count": len(files),
        "files": files,
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }


def host_operations_manifest(root: Path = ROOT) -> dict[str, Any]:
    files = []
    for name in sorted(HOST_OPERATIONAL_FILES | HOST_RELEASE_FILES):
        path = root / name
        if not path.is_file():
            continue
        files.append({"path": name, "sha256": sha256_file(path)})
    canonical = json.dumps(files, separators=(",", ":"), sort_keys=True).encode()
    return {
        "file_count": len(files),
        "files": files,
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _parse_pairs(values: list[str], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} must use name=value syntax")
        name, identity = value.split("=", 1)
        if not name or not identity:
            raise ValueError(f"{label} contains an empty value")
        result[name] = identity
    return result


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    source = runtime_manifest(ROOT)
    commit = args.source_commit or _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    images = _parse_pairs(args.image, "--image")
    image_records = {
        name: {
            "identity": value,
            "immutable": value.startswith("sha256:") or "@sha256:" in value,
        }
        for name, value in sorted(images.items())
    }
    sbom_records: dict[str, Any] = {}
    for value in args.sbom:
        if "=" not in value:
            raise ValueError("--sbom must use name=path syntax")
        name, path_value = value.split("=", 1)
        path = Path(path_value)
        if not path.is_file():
            raise ValueError(f"SBOM file is missing: {name}")
        sbom_records[name] = {
            "format": "SPDX-JSON" if path.suffix == ".json" else "declared-by-build",
            "sha256": sha256_file(path),
            "artifact": name,
            "image_identity": image_records.get(name, {}).get("identity", "UNKNOWN"),
        }
    artifact_digest = None
    if args.artifact:
        artifact = Path(args.artifact)
        if not artifact.is_file():
            raise ValueError("artifact path is missing")
        artifact_digest = sha256_file(artifact)
    release_id = args.release_id or f"{commit[:12]}-{source['sha256'][:12]}"
    gates = {name: status for name, status in _parse_pairs(args.gate, "--gate").items()}
    return {
        "schema_version": "1.0",
        "release_id": release_id,
        "build": {
            "timestamp_utc": args.build_timestamp,
            "builder": args.builder,
            "context": "repository-runtime-source",
        },
        "source": {
            "commit": commit,
            "runtime_manifest_sha256": source["sha256"],
            "runtime_file_count": source["file_count"],
            "dirty_worktree": dirty,
            "authority": "verified-filesystem",
            "scope": "deployable runtime source; host operational and CI/release tooling excluded",
        },
        "host_operations": host_operations_manifest(ROOT),
        "artifacts": {
            "runtime_source_manifest_sha256": source["sha256"],
            "artifact_sha256": artifact_digest,
            "scope": "repository runtime artifact when supplied; current-state bootstrap may link separate artifacts",
        },
        "images": image_records,
        "migration": {
            "head": args.migration_head,
            "required_head": "20260917_0011_password_reset_atomicity",
        },
        "sbom": {"required": True, "artifacts": sbom_records},
        "security_scan": {
            "status": args.scan_status,
            "policy": "HIGH,CRITICAL fail; reviewed exception required",
        },
        "gates": gates,
        "provenance": {
            "manifest_status": "BOOTSTRAPPED FROM VERIFIED PRODUCTION STATE"
            if args.bootstrapped
            else "BUILD_OUTPUT",
            "classification": "BOOTSTRAPPED_CURRENT_STATE"
            if args.bootstrapped
            else "HISTORICAL_BUILD_OUTPUT",
            "attestation": "READY_METADATA_ONLY",
            "attestation_state": "UNSIGNED",
            "signature": "NOT_APPROVED",
            "signature_state": "NOT_ENFORCED",
            "signature_enforced": False,
            "verification_command": "scripts/verify_release.py",
        },
        "production_verification": {
            "status": getattr(args, "production_verification_status", "UNKNOWN"),
            "source": "UNKNOWN",
            "images": "UNKNOWN",
            "migration": "UNKNOWN",
            "sbom": "UNKNOWN",
        },
        "rollback": {
            "status": "NOT_RUN",
            "metadata": "not supplied by this manifest builder",
        },
    }


def validate_manifest(
    manifest: dict[str, Any], *, require_release_ready: bool = False
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version",
        "release_id",
        "source",
        "artifacts",
        "images",
        "migration",
        "sbom",
        "security_scan",
        "gates",
        "provenance",
    }
    errors.extend(f"missing:{field}" for field in sorted(required - set(manifest)))
    if manifest.get("schema_version") != "1.0":
        errors.append("schema_version")
    source = manifest.get("source", {})
    if not re.fullmatch(
        r"[0-9a-f]{64}", str(source.get("runtime_manifest_sha256", ""))
    ):
        errors.append("source.runtime_manifest_sha256")
    if not isinstance(manifest.get("release_id"), str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", str(manifest.get("release_id", ""))
    ):
        errors.append("release_id")

    images = manifest.get("images", {})
    sbom_artifacts = manifest.get("sbom", {}).get("artifacts", {})
    security_scan = manifest.get("security_scan", {})
    security_images = security_scan.get("images", {})
    gates = manifest.get("gates", {})
    for name, status in gates.items() if isinstance(gates, dict) else []:
        if status not in {"PASS", "FAIL", "UNKNOWN"}:
            errors.append(f"gates.{name}.state")

    if require_release_ready:
        expected_names = {"backend", "worker", "caddy"}
        if not isinstance(images, dict) or set(images) != expected_names:
            errors.append("images.required_roles")
        invalid_images = False
        for name in sorted(expected_names):
            record = images.get(name, {}) if isinstance(images, dict) else {}
            identity = record.get("identity") if isinstance(record, dict) else None
            if (
                record.get("immutable") is not True
                or not isinstance(identity, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity)
            ):
                errors.append(f"images.{name}.immutable_identity_required")
                invalid_images = True
        if invalid_images:
            errors.append("images.immutable_identity_required")
        artifact_sha = manifest.get("artifacts", {}).get("artifact_sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", str(artifact_sha or "")):
            errors.append("artifacts.artifact_sha256")
        if (
            manifest.get("provenance", {}).get("classification")
            != "BOOTSTRAPPED_CURRENT_STATE"
        ):
            artifacts = manifest.get("artifacts", {})
            if not re.fullmatch(
                r"[0-9a-f]{64}", str(artifacts.get("oci_image_archive_sha256", ""))
            ) or not isinstance(artifacts.get("oci_image_archive_path"), str):
                errors.append("artifacts.oci_image_archive")
        migration = manifest.get("migration", {})
        if (
            migration.get("head") != migration.get("required_head")
            or migration.get("head") != "20260917_0011_password_reset_atomicity"
        ):
            errors.append("migration.required_head")
        if security_scan.get("status") != "PASS":
            errors.append("security_scan.status")
            errors.append("security_scan")
        if security_scan.get("exceptions") != []:
            errors.append("security_scan.exceptions")
        for name in sorted(expected_names):
            identity = (
                images.get(name, {}).get("identity")
                if isinstance(images, dict)
                else None
            )
            sbom = (
                sbom_artifacts.get(name, {}) if isinstance(sbom_artifacts, dict) else {}
            )
            if (
                not isinstance(sbom, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", str(sbom.get("sha256", "")))
                or sbom.get("image_identity") != identity
                or not isinstance(sbom.get("artifact"), str)
                or not sbom.get("artifact")
            ):
                errors.append(f"sbom.{name}.identity-or-digest")
            scan = (
                security_images.get(name, {})
                if isinstance(security_images, dict)
                else {}
            )
            if (
                not isinstance(scan, dict)
                or scan.get("image_id") != identity
                or scan.get("high") != 0
                or scan.get("critical") != 0
                or scan.get("ignore_unfixed") is not False
                or scan.get("exceptions") is not False
                or not re.fullmatch(r"[0-9a-f]{64}", str(scan.get("sha256", "")))
                or scan.get("sbom_sha256") != sbom.get("sha256")
                or not isinstance(scan.get("artifact"), str)
                or not scan.get("artifact")
            ):
                errors.append(f"security_scan.{name}.policy-or-linkage")
        if not isinstance(sbom_artifacts, dict) or not expected_names.issubset(
            sbom_artifacts
        ):
            errors.append("sbom")
        missing_gates = [
            name
            for name in REQUIRED_RELEASE_GATES
            if not isinstance(gates, dict) or gates.get(name) != "PASS"
        ]
        errors.extend(f"gates.{name}.required-pass" for name in missing_gates)
        host_operations = manifest.get("host_operations", {})
        if not isinstance(host_operations, dict):
            errors.append("host_operations")
            host_operations = {}
        host_files = host_operations.get("files", [])
        if not isinstance(host_files, list):
            errors.append("host_operations.files")
            host_files = []
        host_paths = [
            record.get("path") for record in host_files if isinstance(record, dict)
        ]
        host_names = {path for path in host_paths}
        if (
            host_names != HOST_OPERATIONAL_FILES | HOST_RELEASE_FILES
            or host_paths != sorted(HOST_OPERATIONAL_FILES | HOST_RELEASE_FILES)
        ):
            errors.append("host_operations.required-files")
        for record in host_files if isinstance(host_files, list) else []:
            if not isinstance(record, dict) or not re.fullmatch(
                r"[0-9a-f]{64}", str(record.get("sha256", ""))
            ):
                errors.append("host_operations.file-digest")
                break
        canonical = json.dumps(
            host_files, separators=(",", ":"), sort_keys=True
        ).encode()
        if (
            host_operations.get("file_count") != len(host_files)
            or host_operations.get("sha256") != hashlib.sha256(canonical).hexdigest()
        ):
            errors.append("host_operations.manifest-digest")
        rollback = manifest.get("rollback", {})
        previous = rollback.get("previous", {}) if isinstance(rollback, dict) else {}
        if (
            rollback.get("status") != "PASS"
            or rollback.get("migration_compatible") is not True
            or rollback.get("current_migration_head") != migration.get("head")
            or rollback.get("old_images_deleted") is not False
            or not isinstance(rollback.get("source_identity"), str)
            or not rollback.get("source_identity")
            or not re.fullmatch(r"[0-9a-f]{64}", str(rollback.get("config_sha256", "")))
        ):
            errors.append("rollback.contract")
        for name in sorted(expected_names):
            record = previous.get(name) if isinstance(previous, dict) else None
            if name == "worker" and record is None and isinstance(previous, dict):
                record = previous.get("workers")
            if not isinstance(record, dict) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(record.get("image_id", ""))
            ):
                errors.append(f"rollback.previous.{name}.image_id")
    if isinstance(sbom_artifacts, dict):
        for name, record in sbom_artifacts.items():
            if not isinstance(record, dict) or not re.fullmatch(
                r"[0-9a-f]{64}", str(record.get("sha256", ""))
            ):
                errors.append(f"sbom.{name}.sha256")
    provenance = manifest.get("provenance", {})
    if provenance.get("signature_enforced") is not False:
        errors.append("provenance.signature_enforced_must_be_explicit")
    if provenance.get("attestation_state") not in {"UNSIGNED", "SIGNED"}:
        errors.append("provenance.attestation_state")
    production_verification = manifest.get("production_verification")
    if production_verification is not None and not isinstance(
        production_verification, dict
    ):
        errors.append("production_verification")
    gate_link = manifest.get("release_gate_result")
    if gate_link is not None:
        if (
            not isinstance(gate_link, dict)
            or not isinstance(gate_link.get("path"), str)
            or not gate_link.get("path")
            or not re.fullmatch(r"[0-9a-f]{64}", str(gate_link.get("sha256", "")))
        ):
            errors.append("release_gate_result")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--output", required=True)
    build.add_argument("--release-id")
    build.add_argument("--source-commit")
    build.add_argument("--artifact")
    build.add_argument("--image", action="append", default=[])
    build.add_argument("--sbom", action="append", default=[])
    build.add_argument(
        "--migration-head",
        default=os.getenv("MIGRATION_HEAD", "20260917_0011_password_reset_atomicity"),
    )
    build.add_argument("--scan-status", default="NOT_RUN")
    build.add_argument("--gate", action="append", default=[])
    build.add_argument("--builder", default="local-or-github-actions")
    build.add_argument(
        "--build-timestamp", default=os.getenv("SOURCE_DATE_EPOCH", "UNSET")
    )
    build.add_argument("--bootstrapped", action="store_true")
    source = sub.add_parser("source-manifest")
    source.add_argument("--output", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("manifest")
    validate.add_argument("--release-ready", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "build":
            payload = build_manifest(args)
            Path(args.output).write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "release_id": payload["release_id"],
                        "runtime_manifest_sha256": payload["source"][
                            "runtime_manifest_sha256"
                        ],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "source-manifest":
            payload = runtime_manifest(ROOT)
            Path(args.output).write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "file_count": payload["file_count"],
                        "sha256": payload["sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        payload = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        errors = validate_manifest(payload, require_release_ready=args.release_ready)
        print(
            json.dumps(
                {"status": "PASS" if not errors else "FAIL", "errors": errors},
                sort_keys=True,
            )
        )
        return 0 if not errors else 1
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps({"status": "FAIL", "reason": type(exc).__name__}, sort_keys=True)
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

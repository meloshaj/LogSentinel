#!/usr/bin/env python3
"""Read-only release identity verification for direct Compose deployments."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import asyncpg

try:
    from release_manifest import (
        CI_ONLY_FILES,
        HOST_OPERATIONAL_FILES,
        HOST_RELEASE_FILES,
        runtime_manifest,
        validate_manifest,
    )
except ModuleNotFoundError:  # package import from repository tests
    from scripts.release_manifest import (
        CI_ONLY_FILES,
        HOST_OPERATIONAL_FILES,
        HOST_RELEASE_FILES,
        runtime_manifest,
        validate_manifest,
    )


def _pairs(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("identity values must use name=value syntax")
        name, identity = value.split("=", 1)
        result[name] = identity
    return result


def _docker_identity(container: str) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", container],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _running_compose_images() -> tuple[dict[str, str], dict[str, str]]:
    """Read Compose service and image IDs without capturing environment data."""
    try:
        listed = subprocess.run(
            ["docker", "ps", "--quiet", "--no-trunc"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {}, {}
    services: dict[str, str] = {}
    for container_id in listed.stdout.splitlines():
        if not container_id.strip():
            continue
        try:
            inspected = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.service"}} {{.Image}}',
                    container_id,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        fields = inspected.stdout.strip().split()
        if len(fields) == 2 and fields[1].startswith("sha256:"):
            services[fields[0]] = fields[1]
    roles: dict[str, str] = {}
    if services.get("backend"):
        roles["backend"] = services["backend"]
    if services.get("caddy"):
        roles["caddy"] = services["caddy"]
    worker_roles = {
        name: services.get(name) for name in ("pipeline", "webhook", "archive")
    }
    if all(worker_roles.values()) and len(set(worker_roles.values())) == 1:
        roles["worker"] = next(iter(worker_roles.values())) or ""
    return roles, {name: value or "UNKNOWN" for name, value in worker_roles.items()}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_path(root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or (relative.parts and ":" in relative.parts[0])
    ):
        return None
    candidate = root.joinpath(*relative.parts)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
    except OSError:
        return None
    if (
        resolved_root not in resolved.parents
        or not resolved.is_file()
        or candidate.is_symlink()
    ):
        return None
    return resolved


def _trivy_findings(payload: dict[str, Any]) -> tuple[int, int]:
    results = payload.get("Results")
    if not isinstance(results, list):
        raise ValueError("Trivy result lacks Results array")
    high = critical = 0
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("invalid Trivy result")
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise ValueError("invalid Trivy vulnerabilities")
        for finding in vulnerabilities:
            if not isinstance(finding, dict):
                raise ValueError("invalid Trivy finding")
            high += finding.get("Severity") == "HIGH"
            critical += finding.get("Severity") == "CRITICAL"
    return high, critical


async def _db_head(dsn: str) -> str | None:
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(normalized, timeout=5)
    try:
        value = await connection.fetchval(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        )
        return str(value) if value else None
    finally:
        await connection.close()


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = validate_manifest(manifest, require_release_ready=True)
    if errors:
        return {"status": "FAIL", "reasons": errors}
    evidence_root = (
        Path(args.evidence_root).resolve(strict=True)
        if args.evidence_root
        else manifest_path.parent
    )
    checks: dict[str, Any] = {}
    expected_source = manifest["source"]["runtime_manifest_sha256"]
    actual_source = runtime_manifest(Path(args.source_root))["sha256"]
    checks["source"] = {
        "expected": expected_source,
        "observed": actual_source,
        "match": expected_source == actual_source,
        "scope": manifest["source"].get(
            "scope",
            "deployable runtime source; host operational and CI/release tooling excluded",
        ),
        "host_operational_files_excluded": sorted(HOST_OPERATIONAL_FILES),
        "ci_release_files_excluded": sorted(CI_ONLY_FILES),
    }
    host_root = Path(getattr(args, "host_root", None) or args.source_root)

    observed_images = _pairs(args.image)
    live_images, worker_services = _running_compose_images()
    for name, identity in live_images.items():
        observed_images.setdefault(name, identity)
    for name, container in (
        ("backend", args.backend_container),
        ("caddy", args.frontend_container),
    ):
        if name not in observed_images and container:
            container_identity = _docker_identity(container)
            if container_identity:
                observed_images[name] = container_identity
    image_checks: dict[str, Any] = {}
    for name, expected in manifest.get("images", {}).items():
        observed = observed_images.get(name)
        expected_identity = (
            expected.get("identity") if isinstance(expected, dict) else None
        )
        image_checks[name] = {
            "expected": expected_identity,
            "observed": observed,
            "match": bool(
                observed and expected_identity and observed == expected_identity
            ),
            "known": bool(observed),
        }
    checks["images"] = image_checks
    checks["worker_services"] = worker_services

    sbom_results: dict[str, Any] = {}
    scan_results: dict[str, Any] = {}
    sbom_manifest = manifest.get("sbom", {}).get("artifacts", {})
    security_manifest = manifest.get("security_scan", {}).get("images", {})
    for name, image in manifest.get("images", {}).items():
        expected_image_id = image.get("identity") if isinstance(image, dict) else None
        sbom = sbom_manifest.get(name, {})
        sbom_path = _evidence_path(evidence_root, sbom.get("artifact"))
        sbom_known = sbom_path is not None
        sbom_match = bool(
            sbom_known
            and sbom_path is not None
            and _sha256_file(sbom_path) == sbom.get("sha256")
            and sbom.get("image_identity") == expected_image_id
        )
        sbom_results[name] = {
            "known": sbom_known,
            "match": sbom_match,
            "expected_sha256": sbom.get("sha256"),
            "image_identity": sbom.get("image_identity"),
        }

        scan = security_manifest.get(name, {})
        scan_path = _evidence_path(evidence_root, scan.get("artifact"))
        scan_known = scan_path is not None
        scan_match = False
        observed_counts = None
        if scan_path is not None:
            try:
                scan_payload = json.loads(scan_path.read_text(encoding="utf-8"))
                observed_counts = _trivy_findings(scan_payload)
                scan_match = bool(
                    _sha256_file(scan_path) == scan.get("sha256")
                    and scan.get("image_id") == expected_image_id
                    and observed_counts == (0, 0)
                    and scan.get("high") == 0
                    and scan.get("critical") == 0
                    and scan.get("ignore_unfixed") is False
                    and scan.get("exceptions") is False
                    and scan.get("sbom_sha256") == sbom.get("sha256")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                scan_known = False
        scan_results[name] = {
            "known": scan_known,
            "match": scan_match,
            "image_id": scan.get("image_id"),
            "observed_high_critical": list(observed_counts)
            if observed_counts is not None
            else None,
        }
    checks["sbom"] = {
        "match": bool(sbom_results)
        and all(item["match"] for item in sbom_results.values()),
        "known": bool(sbom_results)
        and all(item["known"] for item in sbom_results.values()),
        "per_image": sbom_results,
    }
    checks["scan"] = {
        "match": bool(scan_results)
        and all(item["match"] for item in scan_results.values()),
        "known": bool(scan_results)
        and all(item["known"] for item in scan_results.values()),
        "per_image": scan_results,
        "policy": {"HIGH": 0, "CRITICAL": 0, "ignore_unfixed": False, "exceptions": []},
    }

    declared_host = manifest.get("host_operations", {})
    declared_files = {
        record.get("path"): record.get("sha256")
        for record in declared_host.get("files", [])
        if isinstance(record, dict)
    }
    host_file_checks: dict[str, Any] = {}
    for name in sorted(HOST_OPERATIONAL_FILES):
        path = host_root / name
        expected = declared_files.get(name)
        known = path.is_file() and bool(expected)
        observed = _sha256_file(path) if known else None
        host_file_checks[name] = {
            "known": known,
            "match": bool(known and observed == expected),
            "expected_sha256": expected,
            "observed_sha256": observed,
        }
    checks["host_only_retention"] = {
        "match": HOST_OPERATIONAL_FILES.issubset(declared_files)
        and all(item["match"] for item in host_file_checks.values()),
        "known": HOST_OPERATIONAL_FILES.issubset(declared_files)
        and all(item["known"] for item in host_file_checks.values()),
        "manifest_sha256": declared_host.get("sha256"),
        "per_file": host_file_checks,
    }
    release_file_checks: dict[str, Any] = {}
    for name in sorted(HOST_RELEASE_FILES):
        path = host_root / name
        expected = declared_files.get(name)
        known = path.is_file() and bool(expected)
        observed = _sha256_file(path) if known else None
        release_file_checks[name] = {
            "known": known,
            "match": bool(known and observed == expected),
            "expected_sha256": expected,
            "observed_sha256": observed,
        }
    checks["host_release_tooling"] = {
        "match": HOST_RELEASE_FILES.issubset(declared_files)
        and all(item["match"] for item in release_file_checks.values()),
        "known": HOST_RELEASE_FILES.issubset(declared_files)
        and all(item["known"] for item in release_file_checks.values()),
        "per_file": release_file_checks,
    }

    gate_link = manifest.get("release_gate_result", {})
    gate_path = _evidence_path(evidence_root, gate_link.get("path"))
    gate_known = gate_path is not None
    gate_match = False
    if gate_path is not None:
        try:
            gate_payload = json.loads(gate_path.read_text(encoding="utf-8"))
            gate_match = bool(
                _sha256_file(gate_path) == gate_link.get("sha256")
                and gate_payload.get("status") == "PASS"
                and gate_payload.get("release_id") == manifest.get("release_id")
                and gate_payload.get("source_manifest_sha256") == expected_source
                and gate_payload.get("details", {}).get("artifact_sha256")
                == manifest.get("artifacts", {}).get("artifact_sha256")
                and gate_payload.get("details", {}).get("oci_image_archive_sha256")
                == manifest.get("artifacts", {}).get("oci_image_archive_sha256")
                and gate_payload.get("details", {}).get(
                    "eligible_for_explicit_deployment"
                )
                is True
            )
        except (OSError, ValueError, json.JSONDecodeError):
            gate_known = False
    checks["release_gate"] = {"known": gate_known, "match": gate_match}

    migration_head = args.migration_head
    if migration_head is None:
        dsn = (
            args.database_url
            or os.getenv("RETENTION_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )
        if dsn:
            try:
                migration_head = await _db_head(dsn)
            except (OSError, ValueError, asyncpg.PostgresError):
                migration_head = None
    expected_head = manifest.get("migration", {}).get("head")
    checks["migration"] = {
        "expected": expected_head,
        "observed": migration_head,
        "match": bool(
            migration_head and expected_head and migration_head == expected_head
        ),
        "known": bool(migration_head),
    }
    mismatches = []
    unknowns = []
    if not checks["source"]["match"]:
        mismatches.append("source")
    for name, check in image_checks.items():
        if not check["known"]:
            unknowns.append(f"image:{name}")
        elif not check["match"]:
            mismatches.append(f"image:{name}")
    for name, record in sbom_results.items():
        if not record["known"]:
            unknowns.append(f"sbom:{name}")
        elif not record["match"]:
            mismatches.append(f"sbom:{name}")
    for name, record in scan_results.items():
        if not record["known"]:
            unknowns.append(f"scan:{name}")
        elif not record["match"]:
            mismatches.append(f"scan:{name}")
    for name, record in {**host_file_checks, **release_file_checks}.items():
        if not record["known"]:
            unknowns.append(f"host-operations:{name}")
        elif not record["match"]:
            mismatches.append(f"host-operations:{name}")
    if not checks["release_gate"]["known"]:
        unknowns.append("release-gate-result")
    elif not checks["release_gate"]["match"]:
        mismatches.append("release-gate-result")
    if not checks["migration"]["known"]:
        unknowns.append("migration")
    elif not checks["migration"]["match"]:
        mismatches.append("migration")
    status = "FAIL" if mismatches else "UNKNOWN" if unknowns else "PASS"
    return {
        "status": status,
        "release_id": manifest.get("release_id"),
        "checks": checks,
        "mismatches": mismatches,
        "unknowns": unknowns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--source-root", default=".")
    parser.add_argument("--host-root")
    parser.add_argument("--evidence-root")
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--backend-container", default="backend")
    parser.add_argument("--frontend-container", default="caddy")
    parser.add_argument("--migration-head")
    parser.add_argument("--database-url")
    args = parser.parse_args()
    try:
        payload = asyncio.run(verify(args))
    except (OSError, ValueError, json.JSONDecodeError):
        payload = {"status": "UNKNOWN", "reason": "verification-input-unavailable"}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

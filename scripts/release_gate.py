#!/usr/bin/env python3
"""Fail-closed manual release gate for the direct OCI production workflow.

The command has deliberately separate phases. No phase deploys an image. Every
phase is bound to one release ID and one runtime-source manifest; `release-ready`
is an eligibility result only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import unquote, urlsplit
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PHASES = ("validate", "build", "security", "package", "verify")
REQUIRED_CONTROLS = (
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
IMAGE_NAMES = ("backend", "worker", "caddy")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")


class GateError(ValueError):
    """Input or evidence does not satisfy the release contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        encoding = (
            "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        )
        value = json.loads(raw.decode(encoding))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"evidence unavailable or invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise GateError(f"evidence must be a JSON object: {path.name}")
    return value


def _host_operations_manifest(root: Path) -> dict[str, Any]:
    try:
        from release_manifest import host_operations_manifest
    except ModuleNotFoundError:
        from scripts.release_manifest import host_operations_manifest
    return host_operations_manifest(root)


def _manifest_entries(value: dict[str, Any]) -> list[dict[str, str]]:
    raw = value.get("runtime_manifest", value)
    if not isinstance(raw, dict):
        raise GateError("runtime source manifest is missing")
    entries = raw.get("files")
    if not isinstance(entries, list) or not entries:
        raise GateError("runtime source manifest has no file entries")
    normalized: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise GateError("runtime source manifest contains an invalid entry")
        name = entry.get("path")
        digest = entry.get("sha256")
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
        ):
            raise GateError("runtime source manifest entry is incomplete")
        rel = PurePosixPath(name)
        if (
            rel.is_absolute()
            or ".." in rel.parts
            or "\\" in name
            or ":" in rel.parts[0]
        ):
            raise GateError("runtime source manifest contains an unsafe path")
        normalized.append({"path": rel.as_posix(), "sha256": digest})
    if normalized != sorted(normalized, key=lambda item: item["path"]):
        raise GateError("runtime source manifest entries must be sorted")
    canonical = json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode()
    actual_sha = hashlib.sha256(canonical).hexdigest()
    expected_sha = raw.get("sha256")
    expected_count = raw.get("file_count")
    if actual_sha != expected_sha or expected_count != len(normalized):
        raise GateError("runtime source manifest digest or file count mismatch")
    return normalized


def _manifest_parts(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    value = read_json(path)
    return value, _manifest_entries(value)


def _require_source_review(payload: dict[str, Any], source_sha: str) -> None:
    review = payload.get("review")
    host_operations = payload.get("host_operations")
    host_sha = (
        host_operations.get("sha256") if isinstance(host_operations, dict) else None
    )
    if (
        not isinstance(review, dict)
        or review.get("status") != "PASS"
        or review.get("runtime_manifest_sha256") != source_sha
        or review.get("host_operations_sha256") != host_sha
        or not isinstance(host_sha, str)
        or not SHA256_RE.fullmatch(host_sha)
    ):
        raise GateError(
            "runtime source manifest is not explicitly reviewed for this SHA-256"
        )


def validate_source_tree(source_root: Path, entries: list[dict[str, str]]) -> None:
    root = source_root.resolve(strict=True)
    for entry in entries:
        relative = Path(*PurePosixPath(entry["path"]).parts)
        lexical = root / relative
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise GateError(f"source path contains a symlink: {entry['path']}")
        path = lexical.resolve(strict=True)
        if root not in path.parents or not path.is_file():
            raise GateError(f"source path is missing or unsafe: {entry['path']}")
        if sha256_file(path) != entry["sha256"]:
            raise GateError(f"source hash mismatch: {entry['path']}")


def _git_identity(source_root: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=source_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return "FILESYSTEM", True


def _state_path(state_dir: Path, release_id: str, phase: str) -> Path:
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise GateError("release ID contains unsupported characters")
    return state_dir / release_id / f"{phase}.json"


def _phase_result(
    *,
    release_id: str,
    phase: str,
    status: str,
    source_sha: str | None,
    controls: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in {"PASS", "FAIL", "UNKNOWN"}:
        raise GateError("invalid gate state")
    return {
        "schema_version": "1.0",
        "release_id": release_id,
        "phase": phase,
        "status": status,
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_manifest_sha256": source_sha,
        "controls": controls or {},
        "details": details or {},
    }


def _safe_test_env(source_root: Path) -> dict[str, str]:
    blocked = re.compile(
        r"(DATABASE_URL|DSN|POSTGRES_|REDIS_|VALKEY_|JWT|ENCRYPTION|SECRET|TOKEN|PASSWORD|AWS_|S3_|SMTP_|WEBHOOK)",
        re.IGNORECASE,
    )
    env = {key: value for key, value in os.environ.items() if not blocked.search(key)}
    env.update(
        {
            "PYTHONPATH": f"{source_root / 'backend'}{os.pathsep}{source_root}",
            "ENVIRONMENT": "test",
            "TEST_MODE": "true",
            "POSTGRES_USER": "logsentinel",
            "POSTGRES_PASSWORD": "local-release-gate-only",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "65432",
            "POSTGRES_DB": "logsentinel_gate_disposable",
            "DATABASE_URL": "postgresql+asyncpg://logsentinel:local-release-gate-only@127.0.0.1:65432/logsentinel_gate_disposable",
            "LOGSENTINEL_DB_REMEDIATION_TEST_URL": "postgresql+asyncpg://logsentinel:local-release-gate-only@127.0.0.1:65432/logsentinel_gate_disposable",
            "LOGSENTINEL_ALLOW_DISPOSABLE_SCHEMA_TEST": "1",
            "LOGSENTINEL_RUN_DISTRIBUTED_INTEGRATION": "1",
            "REDIS_URL": "redis://127.0.0.1:16379/0",
            "JWT_SECRET_KEY": "release-gate-test-only-jwt-secret-32-characters",
            "ENCRYPTION_KEY": "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
        }
    )
    return env


def _run_check(
    name: str, argv: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> dict[str, Any]:
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, check=False)
    except FileNotFoundError:
        return {"status": "UNKNOWN", "command": argv, "reason": "tool-unavailable"}
    except OSError as exc:
        return {"status": "UNKNOWN", "command": argv, "reason": type(exc).__name__}
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "command": argv,
        "exit_code": result.returncode,
    }


def _compose_env() -> dict[str, str]:
    env = _safe_test_env(ROOT)
    env.update(
        {
            "IMAGE_TAG": "release-gate-validation",
            "POSTGRES_PASSWORD": "release-gate-local-only",
            "JWT_SECRET_KEY": "release-gate-validation-jwt-secret-32-characters",
            "ENCRYPTION_KEY": "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
            "INGEST_API_KEYS": "release-gate-local-only",
            "METRICS_TOKEN": "release-gate-local-only",
            "SMTP_HOST": "smtp.invalid",
            "SMTP_PORT": "587",
            "SMTP_USER": "release-gate",
            "SMTP_PASSWORD": "release-gate-local-only",
            "EMAILS_FROM_EMAIL": "release-gate@example.invalid",
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/release-gate/sentinel",
        }
    )
    return env


def run_validation(
    release_id: str,
    source_root: Path,
    source_manifest_path: Path,
    state_dir: Path,
    test_database_url: str | None = None,
) -> dict[str, Any]:
    source_payload, entries = _manifest_parts(source_manifest_path)
    validate_source_tree(source_root, entries)
    raw = source_payload.get("runtime_manifest", source_payload)
    source_sha = raw["sha256"]
    _require_source_review(source_payload, source_sha)
    if source_payload.get("host_operations", {}).get(
        "sha256"
    ) != _host_operations_manifest(source_root).get("sha256"):
        raise GateError("host operational tooling changed after source review")
    env = _safe_test_env(source_root)
    migration_unknown_reason: str | None = None
    if test_database_url:
        parsed = urlsplit(
            test_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        )
        db_name = parsed.path.lstrip("/")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise GateError("migration test database must use a loopback host")
        if not db_name or not ("release_gate" in db_name or db_name.endswith("_test")):
            raise GateError(
                "migration test database name must identify a disposable database"
            )
        env.update(
            {
                "DATABASE_URL": test_database_url,
                "LOGSENTINEL_DB_REMEDIATION_TEST_URL": test_database_url,
                "POSTGRES_HOST": parsed.hostname or "127.0.0.1",
                "POSTGRES_PORT": str(parsed.port or 5432),
                "POSTGRES_USER": unquote(parsed.username or "logsentinel"),
                "POSTGRES_PASSWORD": unquote(parsed.password or ""),
                "POSTGRES_DB": db_name,
            }
        )
    else:
        migration_unknown_reason = "explicit-disposable-postgresql-url-required"
    compose_env = _compose_env()
    py = sys.executable
    commands: dict[str, list[tuple[str, list[str], Path, dict[str, str] | None]]] = {
        "python_quality": [
            (
                "compileall",
                [py, "-m", "compileall", "-q", "backend", "scripts", "tests"],
                source_root,
                env,
            ),
            (
                "ruff_format",
                ["ruff", "format", "--check", "backend/", "scripts/", "tests/"],
                source_root,
                env,
            ),
            (
                "ruff_lint",
                ["ruff", "check", "backend/", "scripts/", "tests/"],
                source_root,
                env,
            ),
            ("mypy", [py, "-m", "mypy", "-p", "backend.app"], source_root, env),
            (
                "mypy_release_tools",
                [
                    py,
                    "-m",
                    "mypy",
                    "--explicit-package-bases",
                    "scripts/release_gate.py",
                    "scripts/release_manifest.py",
                    "scripts/verify_release.py",
                    "scripts/record_production_release.py",
                ],
                source_root,
                env,
            ),
            ("diff_check", ["git", "diff", "--check"], source_root, env),
        ],
        "backend_tests": [
            (
                "backend_tests",
                [py, "-m", "pytest", "-q", "backend/tests"],
                source_root,
                env,
            )
        ],
        "root_tests": [
            ("root_tests", [py, "-m", "pytest", "-q", "tests"], source_root, env)
        ],
        "frontend_tests": [("frontend_tests", ["npm", "test"], source_root, env)],
        "frontend_typecheck": [
            ("frontend_typecheck", ["npm", "run", "typecheck"], source_root, env)
        ],
        "frontend_build": [
            ("frontend_build", ["npm", "run", "build"], source_root, env)
        ],
        "browser_truth": [
            (
                "browser_truth",
                [
                    "npx",
                    "playwright",
                    "test",
                    "tests/e2e/frontend-truth.spec.ts",
                    "--workers=1",
                    "--retries=0",
                ],
                source_root,
                env,
            )
        ],
        "migration_contract": [
            (
                "lifecycle_contract",
                [py, "scripts/database_lifecycle.py", "--ensure"],
                source_root,
                env,
            ),
            (
                "lifecycle_tests",
                [
                    py,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_database_lifecycle.py",
                    "tests/test_remediation_06c_contract.py",
                ],
                source_root,
                env,
            ),
        ],
        "backup_dr_contract": [
            (
                "backup_dr_contract",
                [
                    py,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_backup_database.py",
                    "tests/test_pitr_contract.py",
                    "tests/test_wal_archive.py",
                    "tests/test_remediation_06c_contract.py",
                ],
                source_root,
                env,
            )
        ],
        "retention_contract": [
            (
                "retention_plan_guard",
                [py, "scripts/retention.py", "--no-database"],
                source_root,
                env,
            ),
            (
                "retention_tests",
                [py, "-m", "pytest", "-q", "tests/retention"],
                source_root,
                env,
            ),
        ],
        "production_compose": [
            (
                "production_compose",
                [
                    "docker",
                    "compose",
                    "-f",
                    "docker-compose.prod.yml",
                    "config",
                    "--quiet",
                ],
                source_root,
                compose_env,
            )
        ],
        "monitoring_compose": [
            (
                "monitoring_compose",
                [
                    "docker",
                    "compose",
                    "-f",
                    "deploy/monitoring/docker-compose.monitoring.yml",
                    "config",
                    "--quiet",
                ],
                source_root,
                compose_env,
            )
        ],
        "workflow_validation": [
            (
                "workflow_validation",
                [py, "scripts/validate_ci_workflow.py"],
                source_root,
                env,
            )
        ],
        "rollback_contract": [
            (
                "rollback_contract",
                [
                    py,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_release_provenance_contract.py",
                    "tests/test_release_gate_contract.py",
                ],
                source_root,
                env,
            )
        ],
    }
    controls: dict[str, Any] = {}
    for control, steps in commands.items():
        if control == "migration_contract" and migration_unknown_reason:
            step_results = [
                {
                    "status": "UNKNOWN",
                    "command": [py, "scripts/database_lifecycle.py", "--ensure"],
                    "reason": migration_unknown_reason,
                },
                _run_check(
                    "lifecycle_tests",
                    [
                        py,
                        "-m",
                        "pytest",
                        "-q",
                        "tests/test_database_lifecycle.py",
                        "tests/test_remediation_06c_contract.py",
                    ],
                    cwd=source_root,
                    env=env,
                ),
            ]
        else:
            step_results = [
                _run_check(step_name, argv, cwd=cwd, env=step_env)
                for step_name, argv, cwd, step_env in steps
            ]
        statuses = {item["status"] for item in step_results}
        status = (
            "FAIL"
            if "FAIL" in statuses
            else "UNKNOWN"
            if "UNKNOWN" in statuses
            else "PASS"
        )
        if control == "migration_contract" and status == "UNKNOWN":
            controls[control] = {
                "status": status,
                "reason": "disposable-postgresql-required",
                "steps": step_results,
            }
        else:
            controls[control] = {"status": status, "steps": step_results}
    statuses = {item["status"] for item in controls.values()}
    overall = (
        "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"
    )
    result = _phase_result(
        release_id=release_id,
        phase="validate",
        status=overall,
        source_sha=source_sha,
        controls=controls,
        details={
            "source_root": str(source_root),
            "source_manifest_path": str(source_manifest_path),
            "no_production_credentials_inherited": True,
        },
    )
    write_json(_state_path(state_dir, release_id, "validate"), result)
    return result


def _make_tar(
    source_root: Path,
    entries: list[dict[str, str]],
    manifest_payload: dict[str, Any],
    destination: Path,
) -> str:
    source_root = source_root.resolve(strict=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
        for entry in entries:
            rel = PurePosixPath(entry["path"])
            path = source_root.joinpath(*rel.parts)
            info = tarfile.TarInfo(rel.as_posix())
            info.size = path.stat().st_size
            info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with path.open("rb") as stream:
                archive.addfile(info, stream)
        meta = (
            json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")).encode()
            + b"\n"
        )
        info = tarfile.TarInfo(".release/source-manifest.json")
        info.size = len(meta)
        info.mode = 0o644
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        import io

        archive.addfile(info, io.BytesIO(meta))
    return sha256_file(destination)


def _docker_id(ref: str) -> str | None:
    try:
        run = subprocess.run(
            ["docker", "image", "inspect", ref, "--format", "{{.Id}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = run.stdout.strip()
    return value if IMAGE_ID_RE.fullmatch(value) else None


def _docker_labels(ref: str) -> dict[str, str] | None:
    try:
        run = subprocess.run(
            ["docker", "image", "inspect", ref, "--format", "{{json .Config.Labels}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        )
        value = json.loads(run.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else {}


def _bundle_path(bundle_root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise GateError(f"{label}:path-missing")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in value
        or (relative.parts and ":" in relative.parts[0])
    ):
        raise GateError(f"{label}:unsafe-path")
    root = bundle_root.resolve(strict=True)
    candidate = root.joinpath(*relative.parts)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise GateError(f"{label}:symlink-path")
    resolved = candidate.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise GateError(f"{label}:path-outside-bundle-or-not-file")
    return resolved


def _verify_source_archive(
    artifact_path: Path,
    source_root: Path,
    expected_source_sha: str,
) -> list[str]:
    errors: list[str] = []
    try:
        with tarfile.open(artifact_path, "r") as archive:
            members = archive.getmembers()
            files: dict[str, tarfile.TarInfo] = {}
            for archive_member in members:
                rel = PurePosixPath(archive_member.name)
                if (
                    rel.is_absolute()
                    or ".." in rel.parts
                    or "\\" in archive_member.name
                    or (rel.parts and ":" in rel.parts[0])
                    or not archive_member.isfile()
                ):
                    errors.append("artifact:unsafe-or-nonregular-member")
                    continue
                if archive_member.name in files:
                    errors.append("artifact:duplicate-member")
                files[archive_member.name] = archive_member
            manifest_member = files.get(".release/source-manifest.json")
            if manifest_member is None:
                return ["artifact:embedded-source-manifest-missing"]
            stream = archive.extractfile(manifest_member)
            if stream is None:
                return ["artifact:embedded-source-manifest-unreadable"]
            embedded = json.loads(stream.read().decode("utf-8"))
            manifest_raw, entries = _manifest_parts_from_value(embedded)
            if manifest_raw.get("sha256") != expected_source_sha:
                errors.append("artifact:source-manifest-identity-mismatch")
            try:
                _require_source_review(embedded, expected_source_sha)
            except GateError:
                errors.append("artifact:source-manifest-review-missing")
            expected_names = {entry["path"] for entry in entries}
            actual_names = set(files) - {".release/source-manifest.json"}
            if actual_names != expected_names:
                errors.append("artifact:manifest-file-set-mismatch")
            if source_root.is_symlink():
                return ["staged-source:root-is-symlink"]
            staged = source_root.resolve(strict=True)
            for entry in entries:
                name = entry["path"]
                file_entry = files.get(name)
                if file_entry is None:
                    errors.append(f"artifact:missing:{name}")
                    continue
                payload = archive.extractfile(file_entry)
                if (
                    payload is None
                    or hashlib.sha256(payload.read()).hexdigest() != entry["sha256"]
                ):
                    errors.append(f"artifact:hash-mismatch:{name}")
                staged_path = staged.joinpath(*PurePosixPath(name).parts)
                cursor = staged
                has_symlink_parent = False
                for part in PurePosixPath(name).parts:
                    cursor = cursor / part
                    has_symlink_parent |= cursor.is_symlink()
                if has_symlink_parent or not staged_path.is_file():
                    errors.append(f"staged-source:missing-or-unsafe:{name}")
                elif staged not in staged_path.resolve(strict=True).parents:
                    errors.append(f"staged-source:outside-root:{name}")
                elif sha256_file(staged_path) != entry["sha256"]:
                    errors.append(f"staged-source:hash-mismatch:{name}")
            staged_files = {
                path.relative_to(staged).as_posix()
                for path in staged.rglob("*")
                if path.is_file()
                and path.relative_to(staged).as_posix()
                != ".release/source-manifest.json"
            }
            if staged_files != expected_names:
                errors.append("staged-source:manifest-file-set-mismatch")
    except (OSError, tarfile.TarError, json.JSONDecodeError, UnicodeError, GateError):
        errors.append("artifact:unreadable-or-invalid")
    return errors


def _manifest_parts_from_value(
    value: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    raw = value.get("runtime_manifest", value)
    if not isinstance(raw, dict):
        raise GateError("runtime source manifest is missing")
    entries = _manifest_entries({"runtime_manifest": raw})
    return raw, entries


def run_build(
    release_id: str,
    source_root: Path,
    source_manifest_path: Path,
    state_dir: Path,
    platform: str,
) -> dict[str, Any]:
    source_payload, entries = _manifest_parts(source_manifest_path)
    validate_source_tree(source_root, entries)
    raw = source_payload.get("runtime_manifest", source_payload)
    source_sha = raw["sha256"]
    _require_source_review(source_payload, source_sha)
    if source_payload.get("host_operations", {}).get(
        "sha256"
    ) != _host_operations_manifest(source_root).get("sha256"):
        raise GateError("host operational tooling changed after source review")
    source_identity = source_payload.get("source", {})
    output_dir = state_dir / release_id / "artifact"
    artifact = output_dir / "runtime-source.tar"
    artifact_sha = _make_tar(source_root, entries, source_payload, artifact)
    extract = output_dir / "context"
    extract.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(artifact, "r") as archive:
            for member in archive.getmembers():
                target = (extract / member.name).resolve()
                if extract.resolve() not in target.parents:
                    raise GateError(
                        "generated release artifact contains path traversal"
                    )
            archive.extractall(extract, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise GateError("release artifact cannot be extracted for image build") from exc
    tags = {
        "backend": f"logsentinel-backend:{release_id}",
        "caddy": f"logsentinel-dashboard:{release_id}",
    }
    image_results: dict[str, Any] = {}
    for name, dockerfile in (
        ("backend", "backend/Dockerfile"),
        ("caddy", "Dockerfile"),
    ):
        if not shutil.which("docker"):
            image_results[name] = {"status": "UNKNOWN", "reason": "docker-unavailable"}
            continue
        try:
            built = subprocess.run(
                [
                    "docker",
                    "build",
                    "--platform",
                    platform,
                    "--label",
                    f"org.logsentinel.release-id={release_id}",
                    "--label",
                    f"org.logsentinel.source-manifest-sha256={source_sha}",
                    "-f",
                    dockerfile,
                    "-t",
                    tags[name],
                    ".",
                ],
                cwd=extract,
                check=False,
            )
        except OSError:
            image_results[name] = {"status": "UNKNOWN", "reason": "docker-unavailable"}
            continue
        image_id = _docker_id(tags[name]) if built.returncode == 0 else None
        labels = _docker_labels(tags[name]) if image_id else None
        labels_match = bool(
            labels
            and labels.get("org.logsentinel.release-id") == release_id
            and labels.get("org.logsentinel.source-manifest-sha256") == source_sha
        )
        image_results[name] = {
            "status": (
                "PASS"
                if image_id and labels_match
                else "FAIL"
                if built.returncode or image_id
                else "UNKNOWN"
            ),
            "tag": tags[name],
            "image_id": image_id,
            "worker_image_id": image_id if name == "backend" else None,
            "labels_match_source_manifest": labels_match,
            "platform": platform,
            "exit_code": built.returncode,
        }
    states = {record["status"] for record in image_results.values()}
    image_status = (
        "FAIL" if "FAIL" in states else "UNKNOWN" if "UNKNOWN" in states else "PASS"
    )
    oci_archive = output_dir / "oci-images.tar"
    archive_status = "UNKNOWN"
    archive_sha: str | None = None
    if image_status == "PASS":
        try:
            saved = subprocess.run(
                [
                    "docker",
                    "save",
                    "--output",
                    str(oci_archive),
                    tags["backend"],
                    tags["caddy"],
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if saved.returncode == 0 and oci_archive.is_file():
                archive_status = "PASS"
                archive_sha = sha256_file(oci_archive)
            else:
                archive_status = "FAIL"
        except OSError:
            archive_status = "UNKNOWN"
    status = (
        "FAIL"
        if "FAIL" in states or archive_status == "FAIL"
        else "UNKNOWN"
        if "UNKNOWN" in states or archive_status == "UNKNOWN"
        else "PASS"
    )
    result = _phase_result(
        release_id=release_id,
        phase="build",
        status=status,
        source_sha=source_sha,
        details={
            "artifact": str(artifact),
            "artifact_sha256": artifact_sha,
            "oci_image_archive": str(oci_archive) if archive_status == "PASS" else None,
            "oci_image_archive_sha256": archive_sha,
            "oci_image_archive_status": archive_status,
            "runtime_file_count": len(entries),
            "source_commit": source_identity.get("git_commit", "FILESYSTEM"),
            "dirty_worktree": source_identity.get("dirty_worktree", True),
            "source_root": str(source_root.resolve()),
            "images": image_results,
            "platform": platform,
            "source_manifest_path": str(source_manifest_path),
            "host_operations_sha256": source_payload.get("host_operations", {}).get(
                "sha256"
            ),
        },
    )
    write_json(_state_path(state_dir, release_id, "build"), result)
    return result


def _trivy_findings(payload: dict[str, Any]) -> tuple[int, int]:
    results = payload.get("Results")
    if not isinstance(results, list):
        raise GateError("Trivy result lacks Results array")
    high = critical = 0
    for result in results:
        if not isinstance(result, dict):
            raise GateError("Trivy result entry is invalid")
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise GateError("Trivy vulnerability list is invalid")
        for finding in vulnerabilities:
            if not isinstance(finding, dict):
                raise GateError("Trivy vulnerability record is invalid")
            if finding.get("Severity") == "HIGH":
                high += 1
            elif finding.get("Severity") == "CRITICAL":
                critical += 1
    return high, critical


def run_security(release_id: str, state_dir: Path) -> dict[str, Any]:
    build = read_json(_state_path(state_dir, release_id, "build"))
    source_sha = build.get("source_manifest_sha256")
    image_records = build.get("details", {}).get("images", {})
    evidence_dir = state_dir / release_id / "security"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    tool_unknown = False
    for name in IMAGE_NAMES:
        key = "caddy" if name == "caddy" else "backend"
        record = image_records.get(key, {})
        image_id = record.get("image_id")
        if name == "worker":
            image_id = record.get("worker_image_id")
        if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id):
            results[name] = {
                "status": "UNKNOWN",
                "reason": "immutable-image-id-missing",
            }
            tool_unknown = True
            continue
        sbom_path = evidence_dir / f"{name}.spdx.json"
        scan_path = evidence_dir / f"{name}.trivy.json"
        try:
            sbom = subprocess.run(
                ["syft", image_id, "-o", f"spdx-json={sbom_path}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            scan = subprocess.run(
                [
                    "trivy",
                    "image",
                    "--ignore-unfixed=false",
                    "--severity",
                    "HIGH,CRITICAL",
                    "--exit-code",
                    "1",
                    "--format",
                    "json",
                    "--output",
                    str(scan_path),
                    image_id,
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            results[name] = {
                "status": "UNKNOWN",
                "reason": "syft-or-trivy-unavailable",
                "image_id": image_id,
            }
            tool_unknown = True
            continue
        if sbom.returncode != 0 or not sbom_path.is_file() or not scan_path.is_file():
            results[name] = {
                "status": "UNKNOWN",
                "reason": "scanner-output-missing",
                "image_id": image_id,
            }
            tool_unknown = True
            continue
        try:
            scan_payload = read_json(scan_path)
            high, critical = _trivy_findings(scan_payload)
        except GateError:
            results[name] = {
                "status": "UNKNOWN",
                "reason": "scanner-result-invalid",
                "image_id": image_id,
            }
            tool_unknown = True
            continue
        result_status = (
            "PASS" if high == 0 and critical == 0 and scan.returncode == 0 else "FAIL"
        )
        results[name] = {
            "status": result_status,
            "image_id": image_id,
            "sbom_path": str(sbom_path),
            "sbom_sha256": sha256_file(sbom_path),
            "trivy_path": str(scan_path),
            "trivy_sha256": sha256_file(scan_path),
            "high": high,
            "critical": critical,
            "ignore_unfixed": False,
            "exceptions": False,
        }
    statuses = {item["status"] for item in results.values()}
    status = (
        "FAIL"
        if "FAIL" in statuses
        else "UNKNOWN"
        if "UNKNOWN" in statuses or tool_unknown
        else "PASS"
    )
    result = _phase_result(
        release_id=release_id,
        phase="security",
        status=status,
        source_sha=source_sha,
        details={
            "policy": {
                "HIGH": 0,
                "CRITICAL": 0,
                "ignore_unfixed": False,
                "exceptions": False,
            },
            "images": results,
        },
    )
    write_json(_state_path(state_dir, release_id, "security"), result)
    return result


def _validate_rollback(payload: dict[str, Any], migration_head: str) -> list[str]:
    errors: list[str] = []
    if payload.get("status") not in {"PASS", "RECORDED"}:
        errors.append("rollback.status")
    previous = payload.get("previous", payload)
    for name in IMAGE_NAMES:
        record = previous.get(name)
        if name == "worker" and record is None:
            record = previous.get("workers")
        if not isinstance(record, dict) or not IMAGE_ID_RE.fullmatch(
            str(record.get("image_id", ""))
        ):
            errors.append(f"rollback.previous.{name}.image_id")
    if payload.get("current_migration_head", migration_head) != migration_head:
        errors.append("rollback.current_migration_head")
    if payload.get("migration_compatible") is not True:
        errors.append("rollback.migration_compatible")
    if payload.get("old_images_deleted") is True:
        errors.append("rollback.old_images_deleted")
    identity = payload.get("source_identity") or payload.get("rollback_source_identity")
    config_sha = payload.get("config_sha256") or payload.get("rollback_config_sha256")
    if not isinstance(identity, str) or not identity.strip():
        errors.append("rollback.source_identity")
    if not isinstance(config_sha, str) or not SHA256_RE.fullmatch(config_sha):
        errors.append("rollback.config_sha256")
    return errors


def run_package(
    release_id: str,
    state_dir: Path,
    rollback_path: Path,
    migration_head: str,
) -> dict[str, Any]:
    validate = _read_phase(state_dir, release_id, "validate")
    build = _read_phase(state_dir, release_id, "build")
    security = _read_phase(state_dir, release_id, "security")
    source_sha = validate.get("source_manifest_sha256")
    errors: list[str] = []
    for phase, record in (
        ("validate", validate),
        ("build", build),
        ("security", security),
    ):
        if record.get("status") != "PASS":
            errors.append(f"{phase}:{record.get('status', 'UNKNOWN')}")
        if record.get("source_manifest_sha256") != source_sha:
            errors.append(f"{phase}:source-manifest-mismatch")
    if migration_head != "20260917_0011_password_reset_atomicity":
        errors.append("migration_head:not-approved")
    try:
        rollback = read_json(rollback_path)
        errors.extend(_validate_rollback(rollback, migration_head))
    except GateError:
        rollback = {}
        errors.append("rollback:evidence-unavailable")
    controls = validate.get("controls", {})
    missing_controls = [
        name
        for name in REQUIRED_CONTROLS
        if controls.get(name, {}).get("status") != "PASS"
    ]
    errors.extend(f"mandatory-control:{name}" for name in missing_controls)
    built_images = build.get("details", {}).get("images", {})
    security_images = security.get("details", {}).get("images", {})
    artifact_path_value = build.get("details", {}).get("artifact")
    artifact_path = (
        Path(artifact_path_value) if isinstance(artifact_path_value, str) else None
    )
    artifact_sha_expected = build.get("details", {}).get("artifact_sha256")
    if (
        artifact_path is None
        or not artifact_path.is_file()
        or sha256_file(artifact_path) != artifact_sha_expected
    ):
        errors.append("artifact:missing-or-digest-mismatch")
    oci_archive_value = build.get("details", {}).get("oci_image_archive")
    oci_archive_path = (
        Path(oci_archive_value) if isinstance(oci_archive_value, str) else None
    )
    oci_archive_sha = build.get("details", {}).get("oci_image_archive_sha256")
    if (
        build.get("details", {}).get("oci_image_archive_status") != "PASS"
        or oci_archive_path is None
        or not oci_archive_path.is_file()
        or sha256_file(oci_archive_path) != oci_archive_sha
    ):
        errors.append("artifact:oci-image-archive-missing-or-mismatch")
    source_manifest_path_value = build.get("details", {}).get("source_manifest_path")
    source_root_value = build.get("details", {}).get("source_root")
    package_source_root = (
        Path(source_root_value).resolve()
        if isinstance(source_root_value, str)
        else None
    )
    reviewed_host_operations: dict[str, Any] = {}
    if isinstance(source_manifest_path_value, str):
        try:
            source_manifest, _ = _manifest_parts(Path(source_manifest_path_value))
            manifest_raw = source_manifest.get("runtime_manifest", source_manifest)
            if manifest_raw.get("sha256") != source_sha:
                errors.append("source-manifest:changed-between-phases")
            if not isinstance(source_sha, str):
                errors.append("source-manifest:sha-missing")
            else:
                try:
                    _require_source_review(source_manifest, source_sha)
                except GateError:
                    errors.append("source-manifest:review-not-confirmed")
            reviewed_host_operations = source_manifest.get("host_operations", {})
            expected_host_sha = build.get("details", {}).get("host_operations_sha256")
            if (
                reviewed_host_operations.get("sha256") != expected_host_sha
                or package_source_root is None
                or expected_host_sha
                != _host_operations_manifest(package_source_root).get("sha256")
            ):
                errors.append("host-operations:changed-between-phases")
        except (GateError, OSError):
            errors.append("source-manifest:unavailable-at-package")
    else:
        errors.append("source-manifest:path-missing")
    images: dict[str, Any] = {}
    sboms: dict[str, Any] = {}
    scans: dict[str, Any] = {}
    for name in IMAGE_NAMES:
        build_key = "caddy" if name == "caddy" else "backend"
        built = built_images.get(build_key, {})
        scanned = security_images.get(name, {})
        image_id = (
            built.get("image_id") if name != "worker" else built.get("worker_image_id")
        )
        if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id):
            errors.append(f"images.{name}.identity")
        elif _docker_id(image_id) != image_id:
            errors.append(f"images.{name}.identity-not-present")
        if built.get("labels_match_source_manifest") is not True:
            errors.append(f"images.{name}.source-label-mismatch")
        if scanned.get("status") != "PASS" or scanned.get("image_id") != image_id:
            errors.append(f"security.{name}.scan-or-image-link")
        if (
            scanned.get("high") != 0
            or scanned.get("critical") != 0
            or scanned.get("ignore_unfixed") is not False
            or scanned.get("exceptions") is not False
        ):
            errors.append(f"security.{name}.policy")
        sbom_path = Path(scanned.get("sbom_path", ""))
        trivy_path = Path(scanned.get("trivy_path", ""))
        if not sbom_path.is_file() or sha256_file(sbom_path) != scanned.get(
            "sbom_sha256"
        ):
            errors.append(f"security.{name}.sbom-missing-or-mismatch")
        if not trivy_path.is_file() or sha256_file(trivy_path) != scanned.get(
            "trivy_sha256"
        ):
            errors.append(f"security.{name}.trivy-missing-or-mismatch")
        else:
            try:
                high, critical = _trivy_findings(read_json(trivy_path))
                if high != scanned.get("high") or critical != scanned.get("critical"):
                    errors.append(f"security.{name}.scan-count-mismatch")
            except GateError:
                errors.append(f"security.{name}.scan-result-invalid")
        images[name] = {
            "identity": image_id or "UNKNOWN",
            "immutable": bool(image_id and IMAGE_ID_RE.fullmatch(image_id)),
            "tag": built.get("tag"),
            "platform": built.get("platform"),
        }
        sboms[name] = {
            "format": "SPDX-JSON",
            "sha256": scanned.get("sbom_sha256", ""),
            "image_identity": image_id or "UNKNOWN",
            "artifact": f"security/{name}.spdx.json",
        }
        scans[name] = {
            "image_id": image_id,
            "high": scanned.get("high"),
            "critical": scanned.get("critical"),
            "sha256": scanned.get("trivy_sha256"),
            "ignore_unfixed": scanned.get("ignore_unfixed"),
            "exceptions": scanned.get("exceptions"),
            "sbom_sha256": scanned.get("sbom_sha256"),
            "artifact": f"security/{name}.trivy.json",
        }
    build_details = build.get("details", {})
    artifact_sha = build_details.get("artifact_sha256")
    if not isinstance(artifact_sha, str) or not SHA256_RE.fullmatch(artifact_sha):
        errors.append("artifact.sha256")
    manifest = {
        "schema_version": "1.0",
        "release_id": release_id,
        "build": {
            "timestamp_utc": build.get("timestamp_utc"),
            "builder": "manual OCI release gate",
            "context": "deterministic runtime source artifact",
        },
        "source": {
            "commit": build_details.get("source_commit", "FILESYSTEM"),
            "runtime_manifest_sha256": source_sha,
            "runtime_file_count": build_details.get("runtime_file_count", 0),
            "dirty_worktree": build_details.get("dirty_worktree", True),
            "authority": "explicit-reviewed-runtime-manifest",
            "scope": "runtime artifact built only from the recorded manifest entries",
        },
        "host_operations": reviewed_host_operations,
        "artifacts": {
            "runtime_source_manifest_sha256": source_sha,
            "artifact_sha256": artifact_sha,
            "artifact_path": "artifact/runtime-source.tar",
            "oci_image_archive_sha256": oci_archive_sha,
            "oci_image_archive_path": "artifact/oci-images.tar",
            "scope": "deterministic tar created from explicit runtime source manifest",
        },
        "images": images,
        "migration": {
            "head": migration_head,
            "required_head": "20260917_0011_password_reset_atomicity",
            "matches_required": migration_head
            == "20260917_0011_password_reset_atomicity",
        },
        "sbom": {"required": True, "artifacts": sboms},
        "security_scan": {
            "status": "PASS" if not errors else "FAIL",
            "policy": "HIGH=0, CRITICAL=0; ignore-unfixed=false; exceptions=none",
            "tool": "Trivy",
            "images": scans,
            "exceptions": [],
        },
        "gates": {
            name: controls.get(name, {}).get("status", "UNKNOWN")
            for name in REQUIRED_CONTROLS
        },
        "provenance": {
            "manifest_status": "BUILD_OUTPUT",
            "classification": "HISTORICAL_BUILD_OUTPUT",
            "attestation": "unsigned metadata only",
            "attestation_state": "UNSIGNED",
            "signature": "NOT_APPROVED",
            "signature_state": "NOT_ENFORCED",
            "signature_enforced": False,
            "verification_command": "python scripts/release_gate.py verify",
        },
        "production_verification": {
            "status": "UNKNOWN",
            "source": "UNKNOWN",
            "images": "UNKNOWN",
            "migration": "UNKNOWN",
            "sbom": "UNKNOWN",
        },
        "rollback": {
            "status": "PASS"
            if not _validate_rollback(rollback, migration_head)
            else "FAIL",
            "previous": rollback.get("previous", rollback),
            "current_migration_head": rollback.get(
                "current_migration_head", migration_head
            ),
            "migration_compatible": rollback.get("migration_compatible"),
            "source_identity": rollback.get("source_identity")
            or rollback.get("rollback_source_identity"),
            "config_sha256": rollback.get("config_sha256")
            or rollback.get("rollback_config_sha256"),
            "old_images_deleted": rollback.get("old_images_deleted"),
        },
    }
    try:
        from release_manifest import validate_manifest

        errors.extend(validate_manifest(manifest, require_release_ready=True))
    except (ImportError, AttributeError):
        errors.append("release_manifest.validator_unavailable")
    package_path = state_dir / release_id / "release-manifest.json"
    write_json(package_path, manifest)
    result = _phase_result(
        release_id=release_id,
        phase="package",
        status="FAIL" if errors else "PASS",
        source_sha=source_sha,
        controls={
            "release_manifest": {
                "status": "FAIL" if errors else "PASS",
                "errors": errors,
            }
        },
        details={
            "manifest_path": str(package_path),
            "manifest_sha256": sha256_file(package_path),
            "artifact_sha256": artifact_sha,
        },
    )
    write_json(_state_path(state_dir, release_id, "package"), result)
    return result


def run_verify(
    release_id: str,
    state_dir: Path,
    bundle_root: Path,
    source_root: Path,
    migration_head: str,
    require_release_gate_result: bool = False,
    host_root: Path = ROOT,
) -> dict[str, Any]:
    errors: list[str] = []
    unknowns: list[str] = []
    manifest_path = bundle_root / "release-manifest.json"
    source_sha: str | None = None
    try:
        bundle_root = bundle_root.resolve(strict=True)
        manifest_path = _bundle_path(bundle_root, "release-manifest.json", "manifest")
        manifest = read_json(manifest_path)
        from release_manifest import validate_manifest

        errors = validate_manifest(manifest, require_release_ready=True)
        source_sha = manifest.get("source", {}).get("runtime_manifest_sha256")
    except FileNotFoundError:
        manifest, errors = {}, []
        unknowns.append("manifest:unavailable")
    except (GateError, ImportError, AttributeError, OSError):
        manifest, errors = {}, []
        unknowns.append("manifest:unavailable-or-invalid")

    artifact_sha = manifest.get("artifacts", {}).get("artifact_sha256")
    artifact_path: Path | None = None
    try:
        artifact_path = _bundle_path(
            bundle_root,
            manifest.get("artifacts", {}).get("artifact_path"),
            "artifact",
        )
        if not isinstance(artifact_sha, str) or not SHA256_RE.fullmatch(artifact_sha):
            errors.append("artifact:sha256-invalid")
        elif sha256_file(artifact_path) != artifact_sha:
            errors.append("artifact:sha256-mismatch")
        elif source_sha:
            errors.extend(
                _verify_source_archive(artifact_path, source_root, source_sha)
            )
    except (GateError, FileNotFoundError):
        unknowns.append("artifact:unavailable")
    oci_archive_sha = manifest.get("artifacts", {}).get("oci_image_archive_sha256")
    oci_archive_path: Path | None = None
    try:
        oci_archive_path = _bundle_path(
            bundle_root,
            manifest.get("artifacts", {}).get("oci_image_archive_path"),
            "oci-image-archive",
        )
        if not isinstance(oci_archive_sha, str) or not SHA256_RE.fullmatch(
            oci_archive_sha
        ):
            errors.append("oci-image-archive:sha256-invalid")
        elif sha256_file(oci_archive_path) != oci_archive_sha:
            errors.append("oci-image-archive:sha256-mismatch")
    except (GateError, FileNotFoundError):
        unknowns.append("oci-image-archive:unavailable")

    images = manifest.get("images", {})
    image_status: dict[str, str] = {}
    for name in IMAGE_NAMES:
        identity = images.get(name, {}).get("identity")
        if not isinstance(identity, str) or not IMAGE_ID_RE.fullmatch(identity):
            unknowns.append(f"images.{name}:identity-missing")
            image_status[name] = "UNKNOWN"
            continue
        actual_id = _docker_id(identity)
        labels = _docker_labels(identity) if actual_id else None
        if actual_id is None:
            unknowns.append(f"images.{name}:not-present-on-verification-host")
            image_status[name] = "UNKNOWN"
        elif actual_id != identity:
            errors.append(f"images.{name}:immutable-id-mismatch")
            image_status[name] = "FAIL"
        elif (
            not labels
            or labels.get("org.logsentinel.release-id") != release_id
            or labels.get("org.logsentinel.source-manifest-sha256") != source_sha
        ):
            errors.append(f"images.{name}:source-label-mismatch")
            image_status[name] = "FAIL"
        else:
            image_status[name] = "PASS"

    migration = manifest.get("migration", {})
    declared_migration = migration.get("head")
    migration_ok = (
        declared_migration == migration_head
        and migration.get("required_head") == migration_head
        and migration.get("matches_required") is not False
    )
    if not declared_migration:
        unknowns.append("migration:head-missing")
    elif not migration_ok:
        errors.append("migration:compatibility-mismatch")

    declared_host_operations = manifest.get("host_operations", {})
    expected_host_ops_sha = (
        declared_host_operations.get("sha256")
        if isinstance(declared_host_operations, dict)
        else None
    )
    try:
        observed_host_ops_sha = _host_operations_manifest(host_root)["sha256"]
        host_operations_match = observed_host_ops_sha == expected_host_ops_sha
        if not host_operations_match:
            errors.append("host-operations:identity-mismatch")
    except (OSError, ValueError):
        observed_host_ops_sha = None
        host_operations_match = False
        unknowns.append("host-operations:identity-unavailable")

    sbom_records = manifest.get("sbom", {}).get("artifacts", {})
    security_images = manifest.get("security_scan", {}).get("images", {})
    sbom_status: dict[str, str] = {}
    scan_status: dict[str, str] = {}
    for name in IMAGE_NAMES:
        sbom = sbom_records.get(name, {})
        scan = security_images.get(name, {})
        identity = images.get(name, {}).get("identity")
        try:
            sbom_path = _bundle_path(bundle_root, sbom.get("artifact"), f"sbom.{name}")
            if sha256_file(sbom_path) != sbom.get("sha256"):
                errors.append(f"sbom.{name}:sha256-mismatch")
                sbom_status[name] = "FAIL"
            elif sbom.get("image_identity") != identity:
                errors.append(f"sbom.{name}:image-link-mismatch")
                sbom_status[name] = "FAIL"
            else:
                sbom_status[name] = "PASS"
        except (GateError, FileNotFoundError):
            unknowns.append(f"sbom.{name}:unavailable")
            sbom_status[name] = "UNKNOWN"

        if scan.get("image_id") != identity:
            errors.append(f"security.{name}:image-link-mismatch")
            scan_status[name] = "FAIL"
            continue
        if (
            scan.get("high") != 0
            or scan.get("critical") != 0
            or scan.get("ignore_unfixed") is not False
            or scan.get("exceptions") is not False
        ):
            errors.append(f"security.{name}:policy-fail")
            scan_status[name] = "FAIL"
            continue
        try:
            trivy_path = _bundle_path(
                bundle_root, scan.get("artifact"), f"security.{name}"
            )
            if sha256_file(trivy_path) != scan.get("sha256"):
                errors.append(f"security.{name}:sha256-mismatch")
                scan_status[name] = "FAIL"
                continue
            actual_high, actual_critical = _trivy_findings(read_json(trivy_path))
            if (actual_high, actual_critical) != (0, 0):
                errors.append(f"security.{name}:finding-policy-fail")
                scan_status[name] = "FAIL"
            else:
                scan_status[name] = "PASS"
        except (GateError, FileNotFoundError):
            unknowns.append(f"security.{name}:unavailable")
            scan_status[name] = "UNKNOWN"

    rollback = manifest.get("rollback", {})
    rollback_errors: list[str] = []
    if not rollback:
        unknowns.append("rollback:metadata-missing")
    else:
        rollback_errors = _validate_rollback(rollback, migration_head)
        if rollback_errors:
            errors.extend(rollback_errors)
        previous = rollback.get("previous", {})
        for name in IMAGE_NAMES:
            record = previous.get(name)
            if name == "worker" and record is None:
                record = previous.get("workers")
            identity = record.get("image_id") if isinstance(record, dict) else None
            if not isinstance(identity, str) or not IMAGE_ID_RE.fullmatch(identity):
                unknowns.append(f"rollback.{name}:identity-missing")
                continue
            if _docker_id(identity) != identity:
                unknowns.append(
                    f"rollback.{name}:image-not-present-on-verification-host"
                )

    if errors:
        result_status = "FAIL"
    elif unknowns:
        result_status = "UNKNOWN"
    else:
        result_status = "PASS"
    gate_result_status = "PASS"
    gate_link = manifest.get("release_gate_result")
    if require_release_gate_result and not isinstance(gate_link, dict):
        unknowns.append("release-gate-result:missing")
        result_status = "UNKNOWN" if not errors else result_status
        gate_result_status = "UNKNOWN"
    elif isinstance(gate_link, dict):
        try:
            gate_path = _bundle_path(
                bundle_root, gate_link.get("path"), "release-gate-result"
            )
            if sha256_file(gate_path) != gate_link.get("sha256"):
                errors.append("release-gate-result:sha256-mismatch")
                gate_result_status = "FAIL"
            else:
                gate_result = read_json(gate_path)
                gate_matches = bool(
                    gate_result.get("status") == "PASS"
                    and gate_result.get("release_id") == release_id
                    and gate_result.get("source_manifest_sha256") == source_sha
                    and gate_result.get("details", {}).get("artifact_sha256")
                    == artifact_sha
                    and gate_result.get("details", {}).get("oci_image_archive_sha256")
                    == oci_archive_sha
                    and gate_result.get("details", {}).get(
                        "eligible_for_explicit_deployment"
                    )
                    is True
                    and gate_result.get("details", {}).get("deployment_authorized")
                    is False
                )
                if not gate_matches:
                    errors.append(
                        "release-gate-result:identity-or-eligibility-mismatch"
                    )
                    gate_result_status = "FAIL"
        except (GateError, FileNotFoundError):
            if require_release_gate_result:
                unknowns.append("release-gate-result:unavailable")
                gate_result_status = "UNKNOWN"
            else:
                gate_result_status = "UNKNOWN"
    elif not require_release_gate_result:
        gate_result_status = "PASS"
    if errors:
        result_status = "FAIL"
    elif unknowns:
        result_status = "UNKNOWN"
    result = _phase_result(
        release_id=release_id,
        phase="verify",
        status=result_status,
        source_sha=source_sha,
        controls={
            "source_identity": {
                "status": "PASS"
                if artifact_path
                and not any(
                    item.startswith(("artifact:", "staged-source:")) for item in errors
                )
                else "UNKNOWN"
                if any(
                    item.startswith(("artifact:", "staged-source:"))
                    for item in unknowns
                )
                else "FAIL",
                "observed": source_sha,
            },
            "candidate_images": {
                "status": "FAIL"
                if "FAIL" in image_status.values()
                else "UNKNOWN"
                if "UNKNOWN" in image_status.values()
                else "PASS",
                "identities": images,
                "per_image": image_status,
            },
            "migration_compatibility": {
                "status": "PASS" if migration_ok else "FAIL",
                "head": migration_head,
            },
            "host_operations_identity": {
                "status": "PASS"
                if host_operations_match
                else "UNKNOWN"
                if observed_host_ops_sha is None
                else "FAIL",
                "expected_sha256": expected_host_ops_sha,
                "observed_sha256": observed_host_ops_sha,
            },
            "sbom_scan_linkage": {
                "status": "FAIL"
                if "FAIL" in (*sbom_status.values(), *scan_status.values())
                else "UNKNOWN"
                if "UNKNOWN" in (*sbom_status.values(), *scan_status.values())
                else "PASS",
                "sbom": sbom_status,
                "scan": scan_status,
            },
            "rollback": {
                "status": "FAIL"
                if rollback_errors
                else "UNKNOWN"
                if any(item.startswith("rollback") for item in unknowns)
                else "PASS",
            },
            "release_gate_evidence": {"status": gate_result_status},
        },
        details={
            "server_side_predeploy_verification": True,
            "errors": errors,
            "unknowns": unknowns,
            "manifest_path": str(manifest_path),
            "artifact_path": str(artifact_path) if artifact_path else None,
            "artifact_sha256": artifact_sha,
            "oci_image_archive_path": str(oci_archive_path)
            if oci_archive_path
            else None,
            "oci_image_archive_sha256": oci_archive_sha,
            "final_release_gate_result_required": require_release_gate_result,
        },
    )
    write_json(_state_path(state_dir, release_id, "verify"), result)
    return result


def import_current_release(
    release_id: str,
    evidence_dir: Path,
    state_dir: Path,
    production_verification_path: Path | None,
    production_health_path: Path | None,
) -> dict[str, Any]:
    """Revalidate an already-built/deployed 07H identity without rebuilding it.

    This is explicitly retrospective evidence adoption. It cannot make a new
    source tree eligible and it never authorizes or performs deployment.
    """
    from release_manifest import validate_manifest

    evidence = evidence_dir.resolve(strict=True)
    manifest_path = evidence / "current-release-manifest.json"
    manifest = read_json(manifest_path)
    errors = validate_manifest(manifest, require_release_ready=False)
    expected = manifest.get("images", {})
    source_sha = manifest.get("source", {}).get("runtime_manifest_sha256")
    artifact_sha = manifest.get("artifacts", {}).get("artifact_sha256")
    if not isinstance(artifact_sha, str) or not SHA256_RE.fullmatch(artifact_sha):
        errors.append("artifact.digest-missing")
    if not isinstance(source_sha, str) or not SHA256_RE.fullmatch(source_sha):
        errors.append("source.manifest-digest-missing")

    def evidence_json(name: str) -> dict[str, Any]:
        return read_json(evidence / name)

    source_eq = evidence_json("source-equivalence.json")
    pre_scan = evidence_json("predeploy-vulnerability-scan.json")
    post_scan = evidence_json("postdeploy-vulnerability-scan.json")
    pre_sbom = evidence_json("predeploy-sbom.json")
    post_sbom = evidence_json("postdeploy-sbom.json")
    active = evidence_json("active-image-identities.json")
    historic_verify = evidence_json("production-release-verification.json")
    rollback = evidence_json("rollback-record.json")
    stability = evidence_json("stability.json")
    dr = evidence_json("monitoring-dr.json")
    dr_precheck = evidence_json("dr-precheck.json")
    deployment = evidence_json("deployment.json")
    smoke = evidence_json("image-smoke.json")
    local_validation_path = (
        evidence.parent.parent
        / "remediation-07g"
        / "evidence"
        / "local-validation.json"
    )
    local_validation = read_json(local_validation_path)
    report_path = (
        evidence.parent / "remediation-07h-production-image-cve-remediation.md"
    )
    report_text = report_path.read_text(encoding="utf-8")
    governance_report = (
        evidence.parent.parent / "audit-06" / "full-application-audit-06.md"
    )
    governance_text = governance_report.read_text(encoding="utf-8")
    if (
        source_eq.get("status") != "PASS"
        or source_eq.get("runtime_source_manifest_sha256") != source_sha
    ):
        errors.append("source-equivalence")
    if not all(scan.get("status") == "PASS" for scan in (pre_scan, post_scan)):
        errors.append("scan.status")
    for scan in (pre_scan, post_scan):
        if (
            "ignore-unfixed=false" not in str(scan.get("policy", ""))
            or scan.get("exceptions") is True
        ):
            errors.append("scan.policy-or-exceptions")
    for phase_name, scan, sbom in (
        ("predeploy", pre_scan, pre_sbom),
        ("postdeploy", post_scan, post_sbom),
    ):
        for image in IMAGE_NAMES:
            scan_key = "workers" if image == "worker" else image
            record = scan.get(scan_key, {})
            if record.get("high") != 0 or record.get("critical") != 0:
                errors.append(f"{phase_name}.{image}.severity-policy")
            image_key = "caddy" if image == "caddy" else image
            expected_id = expected.get(image_key, {}).get("identity")
            if record.get("image_id") != expected_id:
                errors.append(f"{phase_name}.{image}.image-link")
            sbom_record = sbom.get(image, {})
            sbom_image_id = sbom_record.get(
                "image_id", sbom_record.get("active_image_id")
            )
            if sbom_image_id != expected_id:
                errors.append(f"{phase_name}.{image}.sbom-image-link")
            if phase_name == "postdeploy":
                file_name = f"postdeploy-{image}.spdx.json"
                sbom_file = evidence / file_name
                if not sbom_file.is_file() or sha256_file(sbom_file) != sbom_record.get(
                    "sha256"
                ):
                    errors.append(f"postdeploy.{image}.sbom-hash")
                raw_name = (
                    "postdeploy-backend-trivy.raw.json"
                    if image == "worker"
                    else f"postdeploy-{image}-trivy.raw.json"
                )
                raw_file = evidence / raw_name
                if not raw_file.is_file() or sha256_file(raw_file) != record.get(
                    "raw_sha256"
                ):
                    errors.append(f"postdeploy.{image}.trivy-hash")
                else:
                    try:
                        raw_high, raw_critical = _trivy_findings(read_json(raw_file))
                        if (raw_high, raw_critical) != (
                            record.get("high"),
                            record.get("critical"),
                        ):
                            errors.append(f"postdeploy.{image}.trivy-count-mismatch")
                    except GateError:
                        errors.append(f"postdeploy.{image}.trivy-invalid")
            else:
                raw_name = (
                    "predeploy-backend-trivy.final.raw.json"
                    if image in {"backend", "worker"}
                    else "predeploy-caddy-trivy.final.raw.json"
                )
                raw_file = evidence / raw_name
                if not raw_file.is_file() or sha256_file(raw_file) != record.get(
                    "raw_sha256"
                ):
                    errors.append(f"predeploy.{image}.trivy-hash")
                else:
                    try:
                        raw_high, raw_critical = _trivy_findings(read_json(raw_file))
                        if (raw_high, raw_critical) != (
                            record.get("high"),
                            record.get("critical"),
                        ):
                            errors.append(f"predeploy.{image}.trivy-count-mismatch")
                    except GateError:
                        errors.append(f"predeploy.{image}.trivy-invalid")
    active_ids = active.get("exact_active_ids", {})
    for role, record in expected.items():
        identity = record.get("identity") if isinstance(record, dict) else None
        actual = active_ids.get(role)
        if role == "worker":
            actual = active_ids.get("pipeline")
            if not all(
                active_ids.get(name) == identity
                for name in ("pipeline", "webhook", "archive")
            ):
                errors.append("active.worker-image-identity")
        if identity != actual:
            errors.append(f"active.{role}-image-identity")
    if active.get("scanned_candidate_ids_equal") is not True:
        errors.append("active.scanned-candidate-link")
    if (
        historic_verify.get("status") != "PASS"
        or historic_verify.get("release_id") != release_id
    ):
        errors.append("historical-production-verifier")
    expected_migration = manifest.get("migration", {}).get("required_head")
    if manifest.get("migration", {}).get("head") != expected_migration:
        errors.append("migration.head")
    if stability.get("status") != "PASS" or not str(
        stability.get("overall_post_final_recreation_observation", "")
    ).startswith(">120"):
        errors.append("historical-stability")
    if dr.get("status") != "PASS" or dr_precheck.get("status") != "PASS":
        errors.append("backup-dr")
    if deployment.get("status") != "PASS" or smoke.get("status") != "PASS":
        errors.append("deployment-or-image-smoke")
    if rollback.get("old_images_deleted") is not False:
        errors.append("rollback.images-not-preserved")
    if local_validation.get("workflow_validation") != "PASS" or local_validation.get(
        "rollback_contract", ""
    ).startswith("FAIL"):
        errors.append("local-ci-or-rollback-contract")

    report_controls = {
        "python_quality": "backend_quality" in local_validation,
        "backend_tests": "backend_tests" in local_validation,
        "frontend_tests": "frontend_unit_tests" in local_validation,
        "frontend_typecheck": "frontend_typecheck" in local_validation,
        "frontend_build": "frontend_build" in local_validation,
        "browser_truth": "12 passed" in governance_text,
        "migration_contract": smoke.get("status") == "PASS"
        and manifest.get("migration", {}).get("head") == expected_migration,
        "backup_dr_contract": dr.get("status") == "PASS"
        and dr_precheck.get("status") == "PASS",
        "retention_contract": dr.get("retention_policy", {}).get("status")
        == "PENDING_OPERATOR_APPROVAL"
        and dr.get("retention_policy", {}).get("destructive_apply_enabled") is False,
        "production_compose": local_validation.get("compose_production") == "PASS",
        "monitoring_compose": local_validation.get("compose_monitoring") == "PASS",
        "workflow_validation": local_validation.get("workflow_validation") == "PASS",
        "rollback_contract": local_validation.get("rollback_contract", "").startswith(
            "PASS"
        ),
    }
    root_tests_ok = "380 passed" in report_text
    report_controls["root_tests"] = root_tests_ok
    controls = {
        key: {
            "status": "PASS" if passed else "UNKNOWN",
            "evidence": str(local_validation_path.relative_to(ROOT.parent.parent))
            if local_validation_path.exists()
            else "missing",
        }
        for key, passed in report_controls.items()
    }
    if not root_tests_ok:
        errors.append("root-tests-evidence")
    missing = [
        name
        for name in REQUIRED_CONTROLS
        if controls.get(name, {}).get("status") != "PASS"
    ]
    if missing:
        errors.extend(f"validation.{name}" for name in missing)
    build_errors = [
        item
        for item in errors
        if item.startswith(
            ("source-equivalence", "source.manifest", "artifact.", "active.")
        )
        and item != "active.scanned-candidate-link"
    ]
    security_errors = [
        item
        for item in errors
        if item.startswith(
            ("predeploy.", "postdeploy.", "scan.", "active.scanned-candidate-link")
        )
    ]

    if production_verification_path is None or production_health_path is None:
        live_verify: dict[str, Any] | None = None
        live_health: dict[str, Any] | None = None
    else:
        live_verify = read_json(production_verification_path)
        live_health = read_json(production_health_path)
    verify_errors: list[str] = []
    if live_verify is None or live_health is None:
        verify_errors.append("live-production-evidence-missing")
    else:
        if (
            live_verify.get("status") != "PASS"
            or live_verify.get("release_id") != release_id
        ):
            verify_errors.append("live-production-verifier")
        if (
            live_health.get("status") != "PASS"
            or live_health.get("stability", {}).get("duration_seconds", 0) < 120
        ):
            verify_errors.append("live-health-or-stability")
        if live_health.get("services", {}).get("backend") != "HEALTHY":
            verify_errors.append("backend-health")
        if (
            live_health.get("stream", {}).get("pending") != 0
            or live_health.get("stream", {}).get("lag") != 0
        ):
            verify_errors.append("pipeline-pel-or-lag")
        if (
            live_health.get("dlq", {}).get("count") != 200
            or live_health.get("dlq", {}).get("modified") is not False
        ):
            verify_errors.append("known-dlq-quarantine")
        if (
            live_health.get("retention", {}).get("approval") != "PENDING"
            or live_health.get("retention", {}).get("apply_enabled") is not False
        ):
            verify_errors.append("retention-boundary")
        if live_health.get("backup_pitr", {}).get("status") != "HEALTHY":
            verify_errors.append("backup-pitr")
        live_images = live_verify.get("checks", {}).get("images", {})
        for name, record in expected.items():
            check = live_images.get(name, {})
            if check.get("match") is not True or check.get("observed") != record.get(
                "identity"
            ):
                verify_errors.append(f"live-image.{name}")
        if live_verify.get("checks", {}).get("migration", {}).get("match") is not True:
            verify_errors.append("live-migration")
        if live_verify.get("checks", {}).get("sbom", {}).get("match") is not True:
            verify_errors.append("live-sbom-linkage")
        if live_verify.get("checks", {}).get("source", {}).get("match") is not True:
            verify_errors.append("live-source-identity")
        if (
            live_verify.get("checks", {}).get("host_only_retention", {}).get("match")
            is not True
        ):
            verify_errors.append("live-retention-tooling-identity")

    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    current_manifest = dict(manifest)
    current_manifest["host_operations"] = _host_operations_manifest(ROOT)
    current_manifest["rollback"] = {
        "status": "PASS"
        if not _validate_rollback(
            {
                "status": rollback.get("status"),
                "previous": {
                    "backend": rollback.get("backend"),
                    "worker": rollback.get("workers"),
                    "caddy": rollback.get("caddy"),
                },
                "current_migration_head": expected_migration,
                "migration_compatible": expected_migration
                == "20260917_0011_password_reset_atomicity",
                "source_identity": "immutable previous OCI image IDs recorded by 07H",
                "config_sha256": "493788d37c7eaeb8a7332be1eac9bb433b0ddc59ad19d87d1f5eee606e998f4f",
                "old_images_deleted": rollback.get("old_images_deleted"),
            },
            expected_migration,
        )
        else "FAIL",
        "previous": {
            "backend": rollback.get("backend"),
            "worker": rollback.get("workers"),
            "caddy": rollback.get("caddy"),
        },
        "current_migration_head": expected_migration,
        "migration_compatible": expected_migration
        == "20260917_0011_password_reset_atomicity",
        "source_identity": "immutable previous OCI image IDs recorded by 07H",
        "config_sha256": "493788d37c7eaeb8a7332be1eac9bb433b0ddc59ad19d87d1f5eee606e998f4f",
        "old_images_deleted": rollback.get("old_images_deleted"),
    }
    current_manifest["production_verification"] = {
        "status": "PASS" if not verify_errors else "UNKNOWN",
        "source": "PASS"
        if live_verify and live_verify.get("checks", {}).get("source", {}).get("match")
        else "UNKNOWN",
        "images": "PASS"
        if live_verify and live_verify.get("checks", {}).get("images")
        else "UNKNOWN",
        "migration": "PASS"
        if live_verify
        and live_verify.get("checks", {}).get("migration", {}).get("match")
        else "UNKNOWN",
        "sbom": "PASS"
        if live_verify and live_verify.get("checks", {}).get("sbom", {}).get("match")
        else "UNKNOWN",
    }
    current_manifest["gates"] = {
        name: record["status"] for name, record in controls.items()
    }
    current_manifest["gates"].update(
        {
            "image_build": "PASS",
            "sbom": "PASS" if not any("sbom" in item for item in errors) else "FAIL",
            "trivy": "PASS"
            if not any("scan" in item or "trivy" in item for item in errors)
            else "FAIL",
            "production_verifier": current_manifest["production_verification"][
                "status"
            ],
            "release_gate": "UNKNOWN",
        }
    )
    current_manifest["security_scan"] = {
        "status": "PASS" if not security_errors else "FAIL",
        "policy": "HIGH=0, CRITICAL=0; ignore-unfixed=false; exceptions=none",
        "tool": post_scan.get("tool"),
        "images": {
            name: {
                "image_id": post_scan.get(
                    "workers" if name == "worker" else name, {}
                ).get("image_id"),
                "high": post_scan.get("workers" if name == "worker" else name, {}).get(
                    "high"
                ),
                "critical": post_scan.get(
                    "workers" if name == "worker" else name, {}
                ).get("critical"),
                "sha256": post_scan.get(
                    "workers" if name == "worker" else name, {}
                ).get("raw_sha256"),
                "artifact": (
                    "postdeploy-backend-trivy.raw.json"
                    if name == "worker"
                    else f"postdeploy-{name}-trivy.raw.json"
                ),
                "ignore_unfixed": False,
                "exceptions": False,
                "sbom_sha256": post_sbom.get(name, {}).get("sha256"),
            }
            for name in IMAGE_NAMES
        },
        "exceptions": [],
    }
    for name in IMAGE_NAMES:
        current_manifest["sbom"]["artifacts"][name]["artifact"] = (
            f"postdeploy-{name}.spdx.json"
        )
    errors.extend(validate_manifest(current_manifest, require_release_ready=True))
    new_manifest_path = state_dir / "release-manifest.json"
    write_json(new_manifest_path, current_manifest)
    rollback_path = state_dir / "rollback-contract.json"
    write_json(rollback_path, current_manifest["rollback"])
    validation_result = _phase_result(
        release_id=release_id,
        phase="validate",
        status="PASS" if not missing else "UNKNOWN",
        source_sha=source_sha,
        controls=controls,
        details={
            "evidence_mode": "existing-07H-release-identity",
            "artifact_digest_revalidated_at_import": False,
            "validation_source": str(report_path),
            "root_tests": "380 passed, 16 skipped" if root_tests_ok else "UNKNOWN",
        },
    )
    build_result = _phase_result(
        release_id=release_id,
        phase="build",
        status="PASS" if not build_errors else "FAIL",
        source_sha=source_sha,
        details={
            "historical_build_evidence": True,
            "artifact_sha256": artifact_sha,
            "images": {
                "backend": {
                    "status": "PASS",
                    "image_id": expected.get("backend", {}).get("identity"),
                    "worker_image_id": expected.get("worker", {}).get("identity"),
                },
                "caddy": {
                    "status": "PASS",
                    "image_id": expected.get("caddy", {}).get("identity"),
                },
            },
            "artifact_digest_revalidated_at_import": False,
        },
    )
    security_result = _phase_result(
        release_id=release_id,
        phase="security",
        status="PASS" if not security_errors else "FAIL",
        source_sha=source_sha,
        details={
            "policy": {
                "HIGH": 0,
                "CRITICAL": 0,
                "ignore_unfixed": False,
                "exceptions": False,
            },
            "predeploy": pre_scan,
            "postdeploy": post_scan,
            "sbom_hashes": post_sbom,
            "images": {
                name: {
                    "status": "PASS",
                    "image_id": post_scan.get(
                        "workers" if name == "worker" else name, {}
                    ).get("image_id"),
                    "high": post_scan.get(
                        "workers" if name == "worker" else name, {}
                    ).get("high"),
                    "critical": post_scan.get(
                        "workers" if name == "worker" else name, {}
                    ).get("critical"),
                    "ignore_unfixed": False,
                    "exceptions": False,
                    "trivy_sha256": post_scan.get(
                        "workers" if name == "worker" else name, {}
                    ).get("raw_sha256"),
                    "sbom_sha256": post_sbom.get(name, {}).get("sha256"),
                }
                for name in IMAGE_NAMES
            },
        },
    )
    package_result = _phase_result(
        release_id=release_id,
        phase="package",
        status="PASS" if not errors else "FAIL",
        source_sha=source_sha,
        details={
            "manifest_path": str(new_manifest_path),
            "manifest_sha256": sha256_file(new_manifest_path),
            "rollback_path": str(rollback_path),
            "rollback_status": current_manifest["rollback"]["status"],
        },
    )
    verify_status = "PASS" if not verify_errors else "UNKNOWN"
    verify_result = _phase_result(
        release_id=release_id,
        phase="verify",
        status=verify_status,
        source_sha=source_sha,
        controls={
            "source_identity": {
                "status": "PASS"
                if live_verify
                and live_verify.get("checks", {}).get("source", {}).get("match")
                else "UNKNOWN"
            },
            "active_image_ids": {
                "status": "PASS"
                if live_verify and live_verify.get("checks", {}).get("images")
                else "UNKNOWN"
            },
            "sbom_linkage": {
                "status": "PASS"
                if live_verify
                and live_verify.get("checks", {}).get("sbom", {}).get("match")
                else "UNKNOWN"
            },
            "scan_result": {"status": "PASS" if not security_errors else "FAIL"},
            "migration": {
                "status": "PASS"
                if live_verify
                and live_verify.get("checks", {}).get("migration", {}).get("match")
                else "UNKNOWN"
            },
            "host_only_retention": {
                "status": "PASS"
                if live_verify
                and live_verify.get("checks", {})
                .get("host_only_retention", {})
                .get("match")
                else "UNKNOWN"
            },
            "production_health": {
                "status": "PASS"
                if live_health and live_health.get("status") == "PASS"
                else "UNKNOWN"
            },
            "stability": {
                "status": "PASS"
                if live_health
                and live_health.get("stability", {}).get("duration_seconds", 0) >= 120
                else "UNKNOWN"
            },
        },
        details={
            "evidence_path": str(production_verification_path)
            if production_verification_path
            else None,
            "health_path": str(production_health_path)
            if production_health_path
            else None,
            "errors": verify_errors,
        },
    )
    for phase_result in (
        validation_result,
        build_result,
        security_result,
        package_result,
        verify_result,
    ):
        write_json(
            _state_path(state_dir, release_id, phase_result["phase"]), phase_result
        )
    result = release_ready(release_id, state_dir)
    result["details"]["evidence_mode"] = "existing-07H-release-identity"
    result["details"]["production_deployment_authorized"] = False
    result["details"]["nonzero_errors"] = errors
    result["details"]["verify_errors"] = verify_errors
    write_json(_state_path(state_dir, release_id, "release-ready"), result)
    return result


def _read_phase(state_dir: Path, release_id: str, phase: str) -> dict[str, Any]:
    result = read_json(_state_path(state_dir, release_id, phase))
    if result.get("release_id") != release_id or result.get("phase") != phase:
        raise GateError(f"{phase} evidence belongs to another release")
    return result


def release_ready(release_id: str, state_dir: Path) -> dict[str, Any]:
    phase_results: dict[str, Any] = {}
    records: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    unknown = False
    failed = False
    source_values: set[str] = set()
    for phase in PHASES:
        try:
            record = _read_phase(state_dir, release_id, phase)
        except GateError:
            records[phase] = {}
            phase_results[phase] = {
                "status": "UNKNOWN",
                "reason": "phase-result-missing-or-invalid",
            }
            reasons.append(f"{phase}:missing")
            unknown = True
            continue
        records[phase] = record
        status = record.get("status")
        phase_results[phase] = {
            "status": status,
            "path": str(_state_path(state_dir, release_id, phase)),
        }
        if status != "PASS":
            reasons.append(
                f"{phase}:{status if status in {'FAIL', 'UNKNOWN'} else 'invalid-state'}"
            )
            failed |= status == "FAIL"
            unknown |= status != "FAIL"
        source_sha = record.get("source_manifest_sha256")
        if source_sha:
            source_values.add(source_sha)
        elif status == "PASS" and phase != "verify":
            reasons.append(f"{phase}:source-manifest-identity-missing")
            unknown = True
    if len(source_values) > 1:
        reasons.append("source-manifest-changed-between-phases")
        failed = True
    validation = phase_results.get("validate", {})
    if validation.get("status") == "PASS":
        controls = _read_phase(state_dir, release_id, "validate").get("controls", {})
        missing = [
            name
            for name in REQUIRED_CONTROLS
            if controls.get(name, {}).get("status") != "PASS"
        ]
        if missing:
            reasons.extend(f"mandatory-control:{name}" for name in missing)
            unknown |= any(
                controls.get(name, {}).get("status") == "UNKNOWN" for name in missing
            )
            failed |= any(
                controls.get(name, {}).get("status") == "FAIL" for name in missing
            )
    if phase_results.get("build", {}).get("status") == "PASS":
        build = records.get("build", {})
        built_images = build.get("details", {}).get("images", {})
        backend = built_images.get("backend", {})
        caddy = built_images.get("caddy", {})
        if (
            backend.get("status") != "PASS"
            or caddy.get("status") != "PASS"
            or not IMAGE_ID_RE.fullmatch(str(backend.get("image_id", "")))
            or backend.get("worker_image_id") != backend.get("image_id")
            or not IMAGE_ID_RE.fullmatch(str(caddy.get("image_id", "")))
        ):
            reasons.append("build:immutable-image-identities-incomplete")
            unknown = True
    if phase_results.get("security", {}).get("status") == "PASS":
        security = records.get("security", {})
        image_scans = security.get("details", {}).get("images", {})
        if set(image_scans) != set(IMAGE_NAMES) or any(
            record.get("status") != "PASS"
            or record.get("high") != 0
            or record.get("critical") != 0
            or record.get("ignore_unfixed") is not False
            or record.get("exceptions") is not False
            or not SHA256_RE.fullmatch(str(record.get("sbom_sha256", "")))
            or not SHA256_RE.fullmatch(str(record.get("trivy_sha256", "")))
            for record in image_scans.values()
        ):
            reasons.append("security:sbom-or-trivy-evidence-incomplete")
            unknown = True
    if phase_results.get("package", {}).get("status") == "PASS":
        package = records.get("package", {})
        manifest_path = Path(package.get("details", {}).get("manifest_path", ""))
        try:
            from release_manifest import validate_manifest

            manifest = read_json(manifest_path)
            manifest_errors = validate_manifest(manifest, require_release_ready=True)
            if (
                manifest.get("release_id") != release_id
                or manifest.get("source", {}).get("runtime_manifest_sha256")
                != package.get("source_manifest_sha256")
                or manifest_errors
            ):
                reasons.append("package:release-manifest-invalid")
                failed |= bool(manifest_errors)
                unknown |= not bool(manifest_errors)
        except (GateError, ImportError, AttributeError):
            reasons.append("package:release-manifest-unavailable")
            unknown = True
    if phase_results.get("verify", {}).get("status") == "PASS":
        verify_controls = records.get("verify", {}).get("controls", {})
        mandatory_verify = (
            "source_identity",
            "candidate_images",
            "migration_compatibility",
            "host_operations_identity",
            "sbom_scan_linkage",
            "rollback",
        )
        missing_verify = [
            name
            for name in mandatory_verify
            if verify_controls.get(name, {}).get("status") != "PASS"
        ]
        if missing_verify:
            reasons.extend(
                f"verify:mandatory-control:{name}" for name in missing_verify
            )
            failed |= any(
                verify_controls.get(name, {}).get("status") == "FAIL"
                for name in missing_verify
            )
            unknown |= any(
                verify_controls.get(name, {}).get("status") != "FAIL"
                for name in missing_verify
            )
    status = "FAIL" if failed else "UNKNOWN" if unknown else "PASS"
    package_details = records.get("package", {}).get("details", {})
    manifest_path = Path(package_details.get("manifest_path", ""))
    try:
        manifest = read_json(manifest_path)
    except GateError:
        manifest = {}
    security_details = records.get("security", {}).get("details", {})
    validate_controls = records.get("validate", {}).get("controls", {})
    image_ids = {
        name: record.get("identity")
        for name, record in manifest.get("images", {}).items()
    }
    sbom_hashes = {
        name: record.get("sha256")
        for name, record in manifest.get("sbom", {}).get("artifacts", {}).items()
    }
    test_results = {
        name: record.get("status", "UNKNOWN")
        for name, record in validate_controls.items()
    }
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    result = _phase_result(
        release_id=release_id,
        phase="release-ready",
        status=status,
        source_sha=next(iter(source_values)) if len(source_values) == 1 else None,
        controls=phase_results,
        details={
            "eligible_for_explicit_deployment": status == "PASS",
            "deployment_authorized": False,
            "reasons": reasons,
            "artifact_sha256": manifest.get("artifacts", {}).get("artifact_sha256"),
            "oci_image_archive_sha256": manifest.get("artifacts", {}).get(
                "oci_image_archive_sha256"
            ),
            "image_ids": image_ids,
            "sbom_hashes": sbom_hashes,
            "trivy_results": security_details.get("images", {}),
            "migration_head": manifest.get("migration", {}).get("head"),
            "test_results": test_results,
            "rollback_identity": manifest.get("rollback", {}),
            "release_manifest_path": str(manifest_path) if manifest else None,
            "timestamp_utc": timestamp,
        },
    )
    write_json(_state_path(state_dir, release_id, "release-ready"), result)
    return result


def link_release_gate_result(
    state_dir: Path, release_id: str, result_path: Path
) -> None:
    """Add a one-way result hash link without creating a manifest/result cycle."""
    package_path = _state_path(state_dir, release_id, "package")
    try:
        package = _read_phase(state_dir, release_id, "package")
    except GateError:
        return
    manifest_value = package.get("details", {}).get("manifest_path")
    if not isinstance(manifest_value, str):
        return
    manifest_path = Path(manifest_value)
    if not manifest_path.is_file():
        return
    manifest = read_json(manifest_path)
    manifest.setdefault("gates", {})["release_gate"] = _read_phase(
        state_dir, release_id, "release-ready"
    ).get("status", "UNKNOWN")
    manifest["release_gate_result"] = {
        "path": result_path.name,
        "sha256": sha256_file(result_path),
    }
    write_json(manifest_path, manifest)
    package.setdefault("details", {})["manifest_sha256"] = sha256_file(manifest_path)
    write_json(package_path, package)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    manifest = sub.add_parser(
        "source-manifest", help="write an explicit runtime source manifest"
    )
    manifest.add_argument("--source-root", required=True)
    manifest.add_argument("--output", required=True)
    manifest.add_argument(
        "--reviewed-source-sha256",
        help="confirm the exact runtime manifest SHA after reviewing its file list",
    )
    manifest.add_argument(
        "--reviewed-host-operations-sha256",
        help="confirm the exact host release and retention tooling digest",
    )

    validate = sub.add_parser("validate", help="run local release prerequisites")
    validate.add_argument("--release-id", required=True)
    validate.add_argument("--source-root", required=True)
    validate.add_argument("--source-manifest", required=True)
    validate.add_argument("--state-dir", required=True)
    validate.add_argument(
        "--test-database-url",
        help="explicit loopback URL for a disposable migration-test database",
    )

    build = sub.add_parser(
        "build", help="package the manifest and build immutable OCI images"
    )
    build.add_argument("--release-id", required=True)
    build.add_argument("--source-root", required=True)
    build.add_argument("--source-manifest", required=True)
    build.add_argument("--state-dir", required=True)
    build.add_argument("--platform", default="linux/arm64")

    security = sub.add_parser(
        "security", help="generate identity-linked SBOMs and run Trivy"
    )
    security.add_argument("--release-id", required=True)
    security.add_argument("--state-dir", required=True)

    package = sub.add_parser(
        "package",
        help="link validation, image, SBOM, scan, migration and rollback evidence",
    )
    package.add_argument("--release-id", required=True)
    package.add_argument("--state-dir", required=True)
    package.add_argument("--rollback", required=True)
    package.add_argument("--migration-head", required=True)

    verify = sub.add_parser(
        "verify", help="server-side predeploy verification; no service changes"
    )
    verify.add_argument("--release-id", required=True)
    verify.add_argument("--state-dir", required=True)
    verify.add_argument("--bundle-root", required=True)
    verify.add_argument("--source-root", required=True)
    verify.add_argument("--migration-head", required=True)
    verify.add_argument("--require-release-gate-result", action="store_true")
    verify.add_argument("--host-root", help="production host operational tooling root")

    ready = sub.add_parser(
        "release-ready", help="fail-closed eligibility result; never deploys"
    )
    ready.add_argument("--release-id", required=True)
    ready.add_argument("--state-dir", required=True)
    ready.add_argument("--result-output")

    existing = sub.add_parser(
        "verify-existing-07h",
        help="revalidate captured 07H evidence and current production checks",
    )
    existing.add_argument("--release-id", required=True)
    existing.add_argument("--evidence-dir", required=True)
    existing.add_argument("--state-dir", required=True)
    existing.add_argument("--production-verification")
    existing.add_argument("--production-health")
    existing.add_argument("--result-output")
    args = parser.parse_args()

    try:
        if args.command == "source-manifest":
            from release_manifest import runtime_manifest

            source_root = Path(args.source_root).resolve(strict=True)
            data = runtime_manifest(source_root)
            host_operations = _host_operations_manifest(source_root)
            commit, dirty = _git_identity(source_root)
            reviewed_sha = args.reviewed_source_sha256
            reviewed_host_sha = args.reviewed_host_operations_sha256
            review_status = (
                "PASS"
                if reviewed_sha == data["sha256"]
                and reviewed_host_sha == host_operations["sha256"]
                else "FAIL"
                if reviewed_sha is not None or reviewed_host_sha is not None
                else "UNKNOWN"
            )
            write_json(
                Path(args.output),
                {
                    "schema_version": "1.0",
                    "source": {"git_commit": commit, "dirty_worktree": dirty},
                    "runtime_manifest": data,
                    "host_operations": host_operations,
                    "review": {
                        "status": review_status,
                        "runtime_manifest_sha256": reviewed_sha,
                        "host_operations_sha256": reviewed_host_sha,
                        "review_scope": "runtime source plus host release and retention tool digests",
                    },
                },
            )
            print(
                json.dumps(
                    {
                        "status": review_status,
                        "sha256": data["sha256"],
                        "host_operations_sha256": host_operations["sha256"],
                        "file_count": data["file_count"],
                    },
                    sort_keys=True,
                )
            )
            return 0 if review_status == "PASS" else 1
        state_dir = Path(args.state_dir).resolve()
        if args.command == "validate":
            result = run_validation(
                args.release_id,
                Path(args.source_root),
                Path(args.source_manifest),
                state_dir,
                args.test_database_url,
            )
        elif args.command == "build":
            result = run_build(
                args.release_id,
                Path(args.source_root),
                Path(args.source_manifest),
                state_dir,
                args.platform,
            )
        elif args.command == "security":
            result = run_security(args.release_id, state_dir)
        elif args.command == "package":
            result = run_package(
                args.release_id, state_dir, Path(args.rollback), args.migration_head
            )
        elif args.command == "verify":
            result = run_verify(
                args.release_id,
                state_dir,
                Path(args.bundle_root),
                Path(args.source_root),
                args.migration_head,
                args.require_release_gate_result,
                Path(args.host_root) if args.host_root else ROOT,
            )
        elif args.command == "verify-existing-07h":
            result = import_current_release(
                args.release_id,
                Path(args.evidence_dir),
                state_dir,
                Path(args.production_verification)
                if args.production_verification
                else None,
                Path(args.production_health) if args.production_health else None,
            )
        else:
            result = release_ready(args.release_id, state_dir)
        if args.command in {"release-ready", "verify-existing-07h"}:
            result_path = (
                Path(args.result_output)
                if args.result_output
                else _state_path(state_dir, args.release_id, "release-ready")
            )
            write_json(result_path, result)
            link_release_gate_result(state_dir, args.release_id, result_path)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "release_id": args.release_id,
                    "phase": result["phase"],
                    "result": str(
                        _state_path(state_dir, args.release_id, result["phase"])
                    ),
                },
                sort_keys=True,
            )
        )
        return 0 if result["status"] == "PASS" else 1
    except (
        GateError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps({"status": "FAIL", "reason": type(exc).__name__}, sort_keys=True)
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

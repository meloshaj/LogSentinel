#!/usr/bin/env python3
"""Validate the repository GitHub Actions supply-chain and release DAG contracts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST_IMAGE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*@sha256:[0-9a-f]{64}$")
MUTABLE_IMAGE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+:[^/\s]+$")
ACTION = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
FROM = re.compile(
    r"^\s*FROM(?:\s+--platform=\S+)?\s+(\S+)(?:\s+AS\s+([A-Za-z0-9_.-]+))?",
    re.MULTILINE | re.IGNORECASE,
)

REQUIRED_RELEASE_JOBS = {
    "quality",
    "backend-test",
    "frontend-test",
    "browser-e2e",
    "migration-contract",
    "backup-dr-contract",
    "retention-contract",
    "deployment-config",
    "helm-reference-validation",
    "image-security",
    "provenance",
    "rollback-contract",
    "release-gate",
}
REQUIRED_RELEASE_GATE_DEPS = REQUIRED_RELEASE_JOBS - {"release-gate"}


def _load_workflow(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # PyYAML 1.1 treats the GitHub Actions key ``on`` as boolean True.
    if True in payload and "on" not in payload:
        payload["on"] = payload.pop(True)
    return payload


def _needs(job: dict[str, Any]) -> list[str]:
    value = job.get("needs", [])
    if isinstance(value, str):
        return [value]
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def _permissions(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _job_images(job: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    services = job.get("services", {})
    if isinstance(services, dict):
        for name, spec in services.items():
            image = spec.get("image") if isinstance(spec, dict) else None
            if isinstance(image, str):
                found.append({"kind": "service", "name": str(name), "image": image})
    container = job.get("container")
    image = (
        container
        if isinstance(container, str)
        else container.get("image")
        if isinstance(container, dict)
        else None
    )
    if isinstance(image, str):
        found.append({"kind": "container", "name": "job", "image": image})
    return found


def _docker_command_images(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not re.search(r"\bdocker\s+(?:run|pull)\b", line):
            continue
        tokens = line.strip().split()
        command_index = next(
            (i for i, token in enumerate(tokens) if token in {"run", "pull"}), None
        )
        if command_index is None:
            continue
        skip_next = False
        for token in tokens[command_index + 1 :]:
            if skip_next:
                skip_next = False
                continue
            if token in {"-v", "--volume", "-w", "--workdir"}:
                skip_next = True
                continue
            if token.startswith("-") or token.startswith("$"):
                continue
            if "/" in token and ("@sha256:" in token or ":" in token):
                found.append(
                    {"kind": "docker-command", "line": str(line_number), "image": token}
                )
                break
    return found


def _ancestors(jobs: dict[str, dict[str, Any]], start: str) -> tuple[set[str], bool]:
    seen: set[str] = set()
    visiting: set[str] = set()
    circular = False

    def visit(name: str) -> None:
        nonlocal circular
        if name in visiting:
            circular = True
            return
        if name in seen:
            return
        visiting.add(name)
        for dependency in _needs(jobs.get(name, {})):
            visit(dependency)
        visiting.remove(name)
        seen.add(name)

    for dependency in _needs(jobs.get(start, {})):
        visit(dependency)
    return seen, circular


def inspect_workflows(workflow_root: Path = WORKFLOW_ROOT) -> dict[str, Any]:
    errors: list[str] = []
    release_gate_security_check = False
    workflows: list[dict[str, Any]] = []
    all_actions: list[dict[str, str]] = []
    all_images: list[dict[str, str]] = []
    all_dockerfiles: list[dict[str, str]] = []
    workflow_paths = sorted(workflow_root.glob("*.y*ml"))

    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        try:
            payload = _load_workflow(path)
        except yaml.YAMLError as exc:
            errors.append(
                f"{path.relative_to(ROOT).as_posix()}:yaml:{type(exc).__name__}"
            )
            continue
        jobs_payload = payload.get("jobs", {})
        jobs = jobs_payload if isinstance(jobs_payload, dict) else {}
        top_permissions = _permissions(payload.get("permissions"))
        actions: list[dict[str, str]] = []
        for reference in ACTION.findall(text):
            if reference.startswith("./"):
                continue
            if "@" not in reference:
                actions.append({"action": reference, "sha": "MISSING"})
                errors.append(f"{path.name}:action-not-pinned:{reference}")
                continue
            action_name, sha = reference.rsplit("@", 1)
            record = {"action": action_name, "sha": sha, "purpose": "workflow action"}
            actions.append(record)
            if not FULL_SHA.fullmatch(sha):
                errors.append(f"{path.name}:action-not-full-sha:{reference}")
        all_actions.extend({"workflow": path.name, **item} for item in actions)

        images = []
        for job_name, raw_job in jobs.items():
            job = raw_job if isinstance(raw_job, dict) else {}
            for image in _job_images(job):
                images.append({"workflow": path.name, "job": str(job_name), **image})
        images.extend(
            {"workflow": path.name, "job": "command", **item}
            for item in _docker_command_images(text)
        )
        all_images.extend(images)
        for item in images:
            image = item["image"]
            if (
                image.startswith("logsentinel-")
                and ":" in image
                and "@sha256:" not in image
            ):
                continue  # local images built in the workflow, not external dependencies
            if not DIGEST_IMAGE.fullmatch(image):
                errors.append(f"{path.name}:mutable-image:{image}")

        job_records = []
        for job_name, raw_job in jobs.items():
            job = raw_job if isinstance(raw_job, dict) else {}
            job_permissions = _permissions(job.get("permissions"))
            writes = sorted(
                key
                for key, value in {**top_permissions, **job_permissions}.items()
                if value == "write"
            )
            if writes:
                errors.append(
                    f"{path.name}:{job_name}:write-permissions:{','.join(writes)}"
                )
            job_records.append(
                {
                    "id": str(job_name),
                    "name": job.get("name"),
                    "needs": _needs(job),
                    "permissions": job_permissions,
                    "images": _job_images(job),
                    "has_artifact_generation": bool(
                        re.search(r"artifact|sbom", text, re.IGNORECASE)
                    ),
                    "has_security_scan": bool(
                        re.search(r"trivy|pip-audit|npm audit", text, re.IGNORECASE)
                    ),
                }
            )

        workflows.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "name": payload.get("name"),
                "triggers": payload.get("on", {}),
                "top_permissions": top_permissions,
                "jobs": job_records,
                "actions": actions,
                "images": images,
            }
        )

        if (
            "contents" not in top_permissions
            or top_permissions.get("contents") != "read"
        ):
            errors.append(f"{path.name}:top-permissions-must-default-to-contents-read")
        if re.search(
            r"continue-on-error\s*:\s*true|\|\|\s*true|^\s*set\s+\+e\b",
            text,
            re.MULTILINE,
        ):
            errors.append(f"{path.name}:failure-suppression-found")
        if path.name == "ci.yml":
            missing = sorted(REQUIRED_RELEASE_JOBS - set(jobs))
            if missing:
                errors.append(f"ci.yml:missing-release-jobs:{','.join(missing)}")
            release = (
                jobs.get("release-gate", {})
                if isinstance(jobs.get("release-gate"), dict)
                else {}
            )
            direct = set(_needs(release))
            missing_direct = sorted(REQUIRED_RELEASE_GATE_DEPS - direct)
            if missing_direct:
                errors.append(
                    f"ci.yml:release-gate-missing-direct-deps:{','.join(missing_direct)}"
                )
            ancestors, circular = _ancestors(
                {str(k): v for k, v in jobs.items() if isinstance(v, dict)},
                "release-gate",
            )
            if circular:
                errors.append("ci.yml:circular-dependency")
            missing_transitive = sorted(REQUIRED_RELEASE_GATE_DEPS - ancestors)
            if missing_transitive:
                errors.append(
                    f"ci.yml:release-gate-missing-transitive-deps:{','.join(missing_transitive)}"
                )
            if "always()" not in str(release.get("if", "")):
                errors.append("ci.yml:release-gate-must-run-to-aggregate-failures")
            for job_name in REQUIRED_RELEASE_JOBS - {"release-gate"}:
                job = jobs.get(job_name, {})
                if (
                    isinstance(job, dict)
                    and "if" in job
                    and job.get("if") not in (None, "")
                ):
                    errors.append(f"ci.yml:mandatory-job-conditional:{job_name}")
            aggregate_text = (
                text[text.find("release-gate:") :] if "release-gate:" in text else ""
            )
            release_gate_security_check = bool(
                re.search(r'test\s+"\$SECURITY"\s+=\s+success', aggregate_text)
                and "needs.image-security.result" in aggregate_text
            )
            for job_name in sorted(REQUIRED_RELEASE_GATE_DEPS):
                if (
                    f"needs.{job_name}.result" not in aggregate_text
                    or "= success" not in aggregate_text
                ):
                    errors.append(
                        f"ci.yml:release-gate-does-not-fail-closed:{job_name}"
                    )

    for dockerfile in sorted(ROOT.rglob("Dockerfile*")):
        if any(
            part in {"node_modules", ".git", "temporary-report", "test-results"}
            for part in dockerfile.parts
        ):
            continue
        stage_aliases: set[str] = set()
        for image, alias in FROM.findall(dockerfile.read_text(encoding="utf-8")):
            if image.lower() in stage_aliases:
                all_dockerfiles.append(
                    {
                        "path": dockerfile.relative_to(ROOT).as_posix(),
                        "image": image,
                        "kind": "internal-stage",
                    }
                )
                if alias:
                    stage_aliases.add(alias.lower())
                continue
            record = {"path": dockerfile.relative_to(ROOT).as_posix(), "image": image}
            all_dockerfiles.append(record)
            if not DIGEST_IMAGE.fullmatch(image):
                errors.append(f"{record['path']}:mutable-base-image:{image}")
            if alias:
                stage_aliases.add(alias.lower())

    ci_jobs = next(
        (
            item["jobs"]
            for item in workflows
            if item["path"] == ".github/workflows/ci.yml"
        ),
        [],
    )
    ci_job_map = {item["id"]: item for item in ci_jobs}
    ci_ancestors, ci_circular = (
        _ancestors(
            {key: {"needs": value["needs"]} for key, value in ci_job_map.items()},
            "release-gate",
        )
        if "release-gate" in ci_job_map
        else (set(), False)
    )
    dag = {
        "workflow": ".github/workflows/ci.yml",
        "job_count": len(ci_job_map),
        "required_job_count": len(REQUIRED_RELEASE_JOBS),
        "release_gate_direct_dependencies": sorted(
            ci_job_map.get("release-gate", {}).get("needs", [])
        ),
        "release_gate_transitive_dependencies": sorted(ci_ancestors),
        "expected_release_gate_dependencies": sorted(REQUIRED_RELEASE_GATE_DEPS),
        "missing_mandatory_dependencies": sorted(
            REQUIRED_RELEASE_GATE_DEPS - ci_ancestors
        ),
        "circular_dependency": ci_circular,
        "optional_bypass_detected": any(
            "if" in item and item["id"] in REQUIRED_RELEASE_GATE_DEPS
            for item in ci_jobs
        ),
        "security_job_result_must_equal_success": release_gate_security_check,
    }
    return {
        "status": "PASS" if not errors else "FAIL",
        "workflow_count": len(workflows),
        "workflows": workflows,
        "actions": all_actions,
        "images": all_images,
        "dockerfile_base_images": all_dockerfiles,
        "dag": dag,
        "failure_semantics": {
            "mandatory_continue_on_error": False,
            "mandatory_ignored_exit_codes": False,
            "release_gate_checks_success_for_all_dependencies": not any(
                item.startswith("ci.yml:release-gate-does-not-fail-closed:")
                for item in errors
            ),
        },
        "permission_writes": [item for item in errors if ":write-permissions:" in item],
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = inspect_workflows()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

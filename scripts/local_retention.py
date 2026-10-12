#!/usr/bin/env python3
"""Plan guarded cleanup for local rollback snapshots and deployment staging."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.retention.engine import (  # noqa: E402
    PolicyError,
    load_policy,
    parse_duration,
    policy_status,
)  # noqa: E402

UTC = dt.timezone.utc
ROLLBACK_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
STAGING_PATTERN = re.compile(r"^logsentinel-[A-Za-z0-9_.-]+-staging-[A-Za-z0-9_.-]+$")


def _mtime(path: Path) -> dt.datetime:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _safe_child(root: Path, candidate: Path) -> bool:
    try:
        return candidate.resolve().parent == root.resolve()
    except OSError:
        return False


def _held(path: Path) -> bool:
    return (path / ".retention-hold").is_file() or (path / ".incident-hold").is_file()


def plan_local(
    rollback_root: Path,
    staging_root: Path,
    *,
    rollback_age: dt.timedelta,
    staging_age: dt.timedelta,
    minimum_snapshots: int,
    active_paths: set[Path],
    referenced_paths: set[Path],
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    if minimum_snapshots < 2:
        raise PolicyError(
            "rollback retention must preserve at least active and previous releases"
        )
    current = now or dt.datetime.now(UTC)
    rollback_entries = (
        sorted(
            (
                entry
                for entry in rollback_root.iterdir()
                if _safe_child(rollback_root, entry)
            ),
            key=_mtime,
            reverse=True,
        )
        if rollback_root.is_dir()
        else []
    )
    rollback_entries = [
        entry for entry in rollback_entries if ROLLBACK_PATTERN.fullmatch(entry.name)
    ]
    protected_by_position = {
        entry.resolve() for entry in rollback_entries[:minimum_snapshots]
    }
    rollback_records: list[dict[str, Any]] = []
    for entry in rollback_entries:
        resolved = entry.resolve()
        reason: str | None = None
        if resolved in protected_by_position:
            reason = "active-or-previous-known-good-position"
        elif resolved in active_paths:
            reason = "active-release"
        elif resolved in referenced_paths:
            reason = "deployment-reference"
        elif _held(entry):
            reason = "incident-or-retention-hold"
        elif _mtime(entry) > current - rollback_age:
            reason = "grace-period"
        rollback_records.append(
            {
                "storage_class": "local.rollback_snapshots",
                "name": entry.name,
                "modified_at": _mtime(entry).isoformat().replace("+00:00", "Z"),
                "action": "PROTECTED" if reason else "ELIGIBLE",
                "blocked_by_guard": reason,
            }
        )

    staging_records: list[dict[str, Any]] = []
    if staging_root.is_dir():
        for entry in sorted(staging_root.iterdir(), key=lambda item: item.name):
            if not entry.is_dir() or not _safe_child(staging_root, entry):
                continue
            if not STAGING_PATTERN.fullmatch(entry.name):
                continue
            resolved = entry.resolve()
            reason: str | None = None
            if resolved in active_paths:
                reason = "active-release"
            elif resolved in referenced_paths:
                reason = "deployment-reference"
            elif _held(entry):
                reason = "incident-or-retention-hold"
            elif _mtime(entry) > current - staging_age:
                reason = "grace-period"
            staging_records.append(
                {
                    "storage_class": "local.staging",
                    "name": entry.name,
                    "modified_at": _mtime(entry).isoformat().replace("+00:00", "Z"),
                    "action": "PROTECTED" if reason else "ELIGIBLE",
                    "blocked_by_guard": reason,
                }
            )
    records = rollback_records + staging_records
    return {
        "status": "PASS",
        "cutoff": {
            "rollback": (current - rollback_age).isoformat().replace("+00:00", "Z"),
            "staging": (current - staging_age).isoformat().replace("+00:00", "Z"),
        },
        "candidate_count": sum(record["action"] == "ELIGIBLE" for record in records),
        "protected_count": sum(record["action"] == "PROTECTED" for record in records),
        "records": records,
        "production_destructive_purge": False,
    }


def apply_local(
    plan: dict[str, Any], rollback_root: Path, staging_root: Path
) -> list[str]:
    deleted: list[str] = []
    for record in plan.get("records", []):
        if record.get("action") != "ELIGIBLE":
            continue
        root = (
            rollback_root
            if record["storage_class"] == "local.rollback_snapshots"
            else staging_root
        )
        target = root / str(record["name"])
        if record[
            "storage_class"
        ] == "local.rollback_snapshots" and not ROLLBACK_PATTERN.fullmatch(target.name):
            raise PolicyError(
                "rollback target name is outside the approved snapshot pattern"
            )
        if record["storage_class"] == "local.staging" and not STAGING_PATTERN.fullmatch(
            target.name
        ):
            raise PolicyError(
                "staging target name is outside the approved prefix pattern"
            )
        if not _safe_child(root, target) or not target.exists() or target.is_symlink():
            raise PolicyError("local-retention target is unsafe or changed")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        deleted.append(record["name"])
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy", default=str(ROOT / "config" / "retention-policy.yml")
    )
    parser.add_argument(
        "--rollback-root", default="/home/ubuntu/logsentinel-deployment-backups"
    )
    parser.add_argument("--staging-root", default="/home/ubuntu")
    parser.add_argument("--active-path", action="append", default=[])
    parser.add_argument("--referenced-path", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        policy = load_policy(args.policy)
        status = policy_status(policy)
        if args.apply and not status["destructive_apply_enabled"]:
            raise PolicyError(
                "local retention apply requires an approved active policy"
            )
        rollback_class = policy["storage_classes"]["local.rollback_snapshots"]
        staging_class = policy["storage_classes"]["local.staging"]
        plan = plan_local(
            Path(args.rollback_root),
            Path(args.staging_root),
            rollback_age=parse_duration(str(rollback_class["retention_duration"]))
            + parse_duration(str(rollback_class["grace_period"])),
            staging_age=parse_duration(str(staging_class["retention_duration"]))
            + parse_duration(str(staging_class["grace_period"])),
            minimum_snapshots=max(2, int(rollback_class["minimum_recovery_points"])),
            active_paths={Path(value).resolve() for value in args.active_path},
            referenced_paths={Path(value).resolve() for value in args.referenced_path},
        )
        payload = {
            "status": "PASS",
            "policy": status,
            "mode": "APPLY" if args.apply else "PLAN",
            "plan": plan,
            "deleted": apply_local(
                plan, Path(args.rollback_root), Path(args.staging_root)
            )
            if args.apply
            else [],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, PolicyError) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": type(exc).__name__}, sort_keys=True
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Plan guarded logical-backup retention; never delete by age alone."""

from __future__ import annotations

import argparse
import datetime as dt
import json
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
    plan_logical_backups,
    plan_pitr_bases,
    plan_wal_objects,
    policy_status,
)

UTC = dt.timezone.utc


def _parse_timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def inspect_local_backups(
    directory: Path, *, remote_verified: bool
) -> list[dict[str, Any]]:
    """Read only safe manifest metadata; no dump contents are returned."""

    from scripts.backup_status import inspect_backup

    records: list[dict[str, Any]] = []
    for manifest_path in sorted(directory.glob("*.dump.manifest.json")):
        observed = inspect_backup(manifest_path)
        created = observed.get("created_at_utc")
        if not isinstance(created, str):
            continue
        records.append(
            {
                "id": manifest_path.name,
                "name": manifest_path.name,
                "created_at": created,
                "valid": observed.get("valid") is True,
                "manifest_valid": observed.get("valid") is True,
                "checksum_verified": observed.get("valid") is True,
                "remote_verified": remote_verified,
                "size_bytes": (
                    manifest_path.with_name(str(observed.get("dump_filename")))
                    .stat()
                    .st_size
                    if observed.get("dump_filename")
                    and manifest_path.with_name(
                        str(observed.get("dump_filename"))
                    ).is_file()
                    else None
                ),
            }
        )
    return records


def plan_directory(
    directory: Path,
    *,
    retention_days: int,
    minimum_valid: int,
    remote_verified: bool,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    if retention_days <= 0 or minimum_valid < 1:
        raise PolicyError("backup retention inputs must be positive")
    current = now or dt.datetime.now(UTC)
    records = inspect_local_backups(directory, remote_verified=remote_verified)
    return plan_logical_backups(
        records,
        cutoff=current - dt.timedelta(days=retention_days),
        minimum_valid_points=minimum_valid,
    )


def apply_local_plan(directory: Path, plan: dict[str, Any]) -> list[str]:
    """Delete only a previously generated eligible set, with revalidation."""

    deleted: list[str] = []
    for record in plan["records"]:
        if record.get("action") != "ELIGIBLE":
            continue
        manifest = directory / str(record["id"])
        if not manifest.name.endswith(".dump.manifest.json"):
            raise PolicyError("unsafe logical-backup manifest name")
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PolicyError(
                "eligible backup manifest changed or is unreadable"
            ) from exc
        dump_name = payload.get("dump_filename")
        if not isinstance(dump_name, str) or Path(dump_name).name != dump_name:
            raise PolicyError("eligible backup dump name is unsafe")
        paths = [manifest, directory / dump_name, directory / f"{dump_name}.sha256"]
        if not all(path.is_file() for path in paths):
            raise PolicyError("eligible backup set is incomplete")
        for path in paths:
            path.unlink()
        deleted.append(manifest.name)
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy", default=str(ROOT / "config" / "retention-policy.yml")
    )
    parser.add_argument(
        "--directory", default="/home/ubuntu/logsentinel-database-backups"
    )
    parser.add_argument("--remote-verified", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--metadata", help="optional JSON fixture with PITR/WAL metadata"
    )
    args = parser.parse_args()
    try:
        policy = load_policy(args.policy)
        status = policy_status(policy)
        if args.apply and not status["destructive_apply_enabled"]:
            raise PolicyError("recovery apply requires an approved active policy")
        if (
            args.apply
            and policy["storage_classes"]["recovery.logical_backups"]["dry_run"]
        ):
            raise PolicyError("logical-backup class remains plan-only")
        logical_policy = policy["storage_classes"]["recovery.logical_backups"]
        pitr_policy = policy["storage_classes"]["recovery.pitr_bases"]
        retention_days = int(
            parse_duration(str(logical_policy["retention_duration"])).total_seconds()
            // 86400
        )
        minimum_valid = int(logical_policy["minimum_recovery_points"])
        logical = plan_directory(
            Path(args.directory),
            retention_days=retention_days,
            minimum_valid=minimum_valid,
            remote_verified=args.remote_verified,
        )
        payload: dict[str, Any] = {
            "status": "PASS",
            "policy": status,
            "mode": "APPLY" if args.apply else "PLAN",
            "logical_backups": logical,
            "deleted": [],
            "production_destructive_purge": False,
        }
        if args.metadata:
            fixture = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
            now = dt.datetime.now(UTC)
            base_plan = plan_pitr_bases(
                fixture.get("pitr_bases", []),
                cutoff=now - parse_duration(str(pitr_policy["retention_duration"])),
                minimum_valid_bases=int(pitr_policy["minimum_recovery_points"]),
            )
            wal_plan = plan_wal_objects(
                fixture.get("wal", []),
                retained_base_ids=set(base_plan["retained_base_ids"]),
            )
            payload["pitr_bases"] = base_plan
            payload["wal"] = wal_plan
        if args.apply:
            payload["deleted"] = apply_local_plan(Path(args.directory), logical)
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

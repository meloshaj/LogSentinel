"""Policy validation and deterministic, fail-closed retention planning.

The module deliberately separates policy, implementation, and enforcement. A
valid proposal can produce a plan, but only an explicitly approved policy can
enable destructive execution. Recovery assets use metadata-only planners so
tests never need production object-store credentials.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - production image installs PyYAML
    yaml = None


UTC = dt.timezone.utc
KNOWN_STORAGE_CLASSES = frozenset(
    {
        "postgres.raw_logs",
        "postgres.feature_inputs",
        "postgres.feature_windows",
        "postgres.anomaly_events",
        "postgres.tracking_loops",
        "postgres.incidents",
        "postgres.pipeline_ledger",
        "postgres.pipeline_outbox",
        "postgres.email_outbox",
        "postgres.security_audit",
        "postgres.auth_sessions",
        "postgres.auth_refresh_tokens",
        "postgres.password_reset_tokens",
        "postgres.migration_ledger",
        "archive.payloads",
        "archive.manifests",
        "archive.rehydration_sessions",
        "recovery.logical_backups",
        "recovery.pitr_bases",
        "recovery.wal",
        "valkey.stream",
        "valkey.dlq",
        "valkey.auth_cache",
        "valkey.rate_limit",
        "monitoring.prometheus",
        "monitoring.alertmanager",
        "local.rollback_snapshots",
        "local.staging",
    }
)


class PolicyError(ValueError):
    """Raised when policy input is malformed or unsafe."""


def _default_policy_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "retention-policy.yml"


def _read_document(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError("retention policy is unreadable") from exc
    try:
        if yaml is not None:
            value = yaml.safe_load(raw)
        else:
            value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise PolicyError("retention policy is malformed") from exc
    except Exception as exc:
        # PyYAML raises yaml.YAMLError subclasses for malformed YAML; keep
        # those parse failures inside the same fail-closed policy boundary.
        if yaml is not None and isinstance(exc, yaml.YAMLError):
            raise PolicyError("retention policy is malformed") from exc
        raise
    if not isinstance(value, dict):
        raise PolicyError("retention policy must be an object")
    return value


def _require_type(value: Any, expected: type, field: str) -> Any:
    if not isinstance(value, expected):
        raise PolicyError(f"retention policy field {field} has the wrong type")
    return value


def _validate(policy: dict[str, Any]) -> dict[str, Any]:
    if policy.get("schema_version") != "1.0":
        raise PolicyError("unsupported retention policy schema version")
    if (
        not isinstance(policy.get("policy_version"), str)
        or not policy["policy_version"].strip()
    ):
        raise PolicyError("retention policy version is required")
    if not isinstance(policy.get("owner"), str) or not policy["owner"].strip():
        raise PolicyError("retention policy owner is required")

    approval = _require_type(policy.get("approval"), dict, "approval")
    approval_status = approval.get("status")
    if approval_status not in {"pending", "approved"}:
        raise PolicyError("retention policy approval status is invalid")
    if approval_status == "approved":
        for field in ("marker", "reference", "approved_at"):
            if not isinstance(approval.get(field), str) or not approval[field].strip():
                raise PolicyError(
                    f"approved retention policy requires approval.{field}"
                )
        if (
            not isinstance(policy.get("effective_date"), str)
            or not policy["effective_date"].strip()
        ):
            raise PolicyError("approved retention policy requires effective_date")
    elif any(approval.get(field) for field in ("marker", "reference", "approved_at")):
        raise PolicyError("pending retention policy cannot contain approval evidence")

    mode = _require_type(policy.get("mode"), dict, "mode")
    if not isinstance(mode.get("dry_run"), bool) or not isinstance(
        mode.get("destructive_apply_enabled"), bool
    ):
        raise PolicyError("retention mode flags must be boolean")
    if not isinstance(mode.get("default_batch_size"), int) or not (
        1 <= mode["default_batch_size"] <= 100_000
    ):
        raise PolicyError("retention default batch size is unsafe")
    if not isinstance(mode.get("max_runtime_seconds"), int) or not (
        1 <= mode["max_runtime_seconds"] <= 86_400
    ):
        raise PolicyError("retention max runtime is unsafe")
    if not isinstance(mode.get("lock_key"), str) or not mode["lock_key"].strip():
        raise PolicyError("retention lock key is required")
    if approval_status != "approved" and (
        mode["dry_run"] is not True or mode["destructive_apply_enabled"] is not False
    ):
        raise PolicyError("unapproved retention policy must remain plan-only")

    classes = _require_type(policy.get("storage_classes"), dict, "storage_classes")
    unknown = set(classes) - KNOWN_STORAGE_CLASSES
    missing = KNOWN_STORAGE_CLASSES - set(classes)
    if unknown:
        raise PolicyError("retention policy contains an unknown storage class")
    if missing:
        raise PolicyError("retention policy does not cover every storage class")
    for class_name, definition in classes.items():
        item = _require_type(definition, dict, f"storage_classes.{class_name}")
        if not isinstance(item.get("enabled"), bool):
            raise PolicyError(f"{class_name}.enabled must be boolean")
        duration = item.get("retention_duration")
        if not isinstance(duration, str) or not duration.strip():
            raise PolicyError(f"{class_name}.retention_duration is required")
        if not isinstance(item.get("dry_run"), bool) or not isinstance(
            item.get("batch_size"), int
        ):
            raise PolicyError(f"{class_name} execution fields are malformed")
        if item["batch_size"] < 0 or item["batch_size"] > 100_000:
            raise PolicyError(f"{class_name}.batch_size is unsafe")
        if item["enabled"] and duration.lower() in {"never", "operator-managed"}:
            # A class may be enabled for inventory while remaining non-purgeable.
            item["purgeable"] = False
        else:
            item["purgeable"] = item["enabled"]
        if not isinstance(item.get("dependency_guards"), list) or not all(
            isinstance(guard, str) and guard.strip()
            for guard in item["dependency_guards"]
        ):
            raise PolicyError(f"{class_name}.dependency_guards are malformed")
    return policy


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the canonical policy; malformed input fails closed."""

    return _validate(_read_document(Path(path) if path else _default_policy_path()))


def policy_status(policy: dict[str, Any]) -> dict[str, Any]:
    """Return safe status metadata without copying approval identity data."""

    _validate(policy)
    approval = policy["approval"]
    approved = approval["status"] == "approved"
    destructive = approved and policy["mode"]["destructive_apply_enabled"]
    return {
        "policy_version": policy["policy_version"],
        "schema_version": policy["schema_version"],
        "effective_date": policy.get("effective_date"),
        "approval_status": "APPROVED" if approved else "PENDING_OPERATOR_APPROVAL",
        "approval_reference_present": bool(approval.get("reference")),
        "mode": "ACTIVE"
        if destructive and not policy["mode"]["dry_run"]
        else "PLAN-ONLY",
        "destructive_apply_enabled": destructive and not policy["mode"]["dry_run"],
        "storage_class_count": len(policy["storage_classes"]),
    }


def parse_duration(value: str) -> dt.timedelta | None:
    """Parse a conservative duration subset used by the policy file."""

    normalized = value.strip().lower()
    if normalized in {"never", "chain-derived", "operator-managed"}:
        return None
    if len(normalized) < 2 or not normalized[:-1].isdigit():
        raise PolicyError("retention duration is not a supported duration")
    amount = int(normalized[:-1])
    unit = normalized[-1]
    if amount <= 0 or unit not in {"h", "d", "w"}:
        raise PolicyError("retention duration is not a positive duration")
    return dt.timedelta(
        **{"hours": amount}
        if unit == "h"
        else {"days": amount * (7 if unit == "w" else 1)}
    )


def _timestamp(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise PolicyError("recovery metadata timestamp is invalid")
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _safe_record(record: dict[str, Any], *, identifier: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "created_at": record.get("created_at"),
        "size_bytes": record.get("size_bytes")
        if isinstance(record.get("size_bytes"), int)
        else None,
        "action": "RETAIN",
        "guard": None,
    }


def plan_logical_backups(
    records: Iterable[dict[str, Any]],
    *,
    cutoff: dt.datetime,
    minimum_valid_points: int,
) -> dict[str, Any]:
    """Plan logical-backup cleanup while retaining the last valid chain."""

    if minimum_valid_points < 1:
        raise PolicyError("minimum valid logical backups must be at least one")
    normalized: list[tuple[dt.datetime, dict[str, Any]]] = []
    output: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        identifier = str(record.get("id") or record.get("name") or f"record-{index}")
        item = _safe_record(record, identifier=identifier)
        created = _timestamp(record.get("created_at"))
        item["created_at"] = created.isoformat().replace("+00:00", "Z")
        valid = all(
            record.get(flag) is True
            for flag in (
                "valid",
                "manifest_valid",
                "checksum_verified",
                "remote_verified",
            )
        )
        item["valid"] = valid
        if valid:
            normalized.append((created, item))
        output.append(item)
    normalized.sort(key=lambda pair: (pair[0], pair[1]["id"]))
    latest_id = normalized[-1][1]["id"] if normalized else None
    removable_slots = max(0, len(normalized) - minimum_valid_points)
    eligible_valid = [
        item
        for created, item in normalized
        if created < cutoff.astimezone(UTC) and item["id"] != latest_id
    ][:removable_slots]
    eligible_ids = {item["id"] for item in eligible_valid}
    for item in output:
        if not item["valid"]:
            item["guard"] = "invalid-or-incomplete-recovery-point"
        elif item["id"] == latest_id:
            item["guard"] = "newest-valid-recovery-point"
        elif item["id"] in eligible_ids:
            item["action"] = "ELIGIBLE"
            item["guard"] = "minimum-valid-recovery-points-preserved"
        else:
            item["guard"] = "minimum-valid-recovery-points-or-age"
    return {
        "kind": "logical_backup",
        "cutoff": cutoff.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "minimum_valid_points": minimum_valid_points,
        "valid_count": len(normalized),
        "newest_valid_id": latest_id,
        "records": output,
        "safe_to_apply": all(
            item["action"] != "ELIGIBLE" or item["valid"] for item in output
        ),
    }


def plan_pitr_bases(
    records: Iterable[dict[str, Any]],
    *,
    cutoff: dt.datetime,
    minimum_valid_bases: int,
) -> dict[str, Any]:
    """Plan base cleanup only when another independently usable base exists."""

    if minimum_valid_bases < 1:
        raise PolicyError("minimum valid PITR bases must be at least one")
    output: list[dict[str, Any]] = []
    usable: list[tuple[dt.datetime, dict[str, Any]]] = []
    for index, record in enumerate(records):
        identifier = str(record.get("id") or record.get("name") or f"base-{index}")
        item = _safe_record(record, identifier=identifier)
        created = _timestamp(record.get("created_at"))
        item["created_at"] = created.isoformat().replace("+00:00", "Z")
        usable_flag = all(
            record.get(flag) is True
            for flag in ("valid", "manifest_valid", "independently_usable")
        )
        item["usable"] = usable_flag
        if usable_flag:
            usable.append((created, item))
        output.append(item)
    usable.sort(key=lambda pair: (pair[0], pair[1]["id"]))
    retained_ids = {item["id"] for _, item in usable[-minimum_valid_bases:]}
    newest_id = usable[-1][1]["id"] if usable else None
    for item in output:
        if not item["usable"]:
            item["guard"] = "invalid-or-not-independently-usable"
        elif item["id"] in retained_ids or item["id"] == newest_id:
            item["guard"] = "minimum-valid-base-and-newest-protected"
        elif _timestamp(item["created_at"]) >= cutoff.astimezone(UTC):
            item["guard"] = "base-not-old-enough"
        else:
            item["action"] = "ELIGIBLE"
            item["guard"] = "newer-independent-base-protects-chain"
    return {
        "kind": "pitr_base",
        "cutoff": cutoff.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "minimum_valid_bases": minimum_valid_bases,
        "usable_count": len(usable),
        "newest_usable_id": newest_id,
        "retained_base_ids": sorted(retained_ids),
        "records": output,
        "safe_to_apply": all(
            item["action"] != "ELIGIBLE"
            or item["guard"] == "newer-independent-base-protects-chain"
            for item in output
        ),
    }


def plan_wal_objects(
    records: Iterable[dict[str, Any]], *, retained_base_ids: set[str]
) -> dict[str, Any]:
    """Retain every WAL object referenced by a retained PITR base chain."""

    output: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        identifier = str(record.get("id") or record.get("name") or f"wal-{index}")
        item = {"id": identifier, "action": "RETAIN", "guard": None}
        dependencies = record.get("required_by_base_ids")
        if not isinstance(dependencies, list) or not all(
            isinstance(value, str) for value in dependencies
        ):
            item["guard"] = "unknown-chain-dependency"
        elif set(dependencies) & retained_base_ids:
            item["guard"] = "retained-base-chain"
        else:
            item["action"] = "ELIGIBLE"
            item["guard"] = "no-retained-recovery-path-depends-on-object"
        output.append(item)
    return {
        "kind": "wal",
        "retained_base_ids": sorted(retained_base_ids),
        "records": output,
        "safe_to_apply": all(
            item["guard"] != "unknown-chain-dependency" for item in output
        ),
    }


def plan_archive_objects(
    records: Iterable[dict[str, Any]], *, cutoff: dt.datetime
) -> dict[str, Any]:
    """Require object/manifest/database consistency before archive cleanup."""

    output: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        identifier = str(
            record.get("id") or record.get("object_key") or f"archive-{index}"
        )
        item = {"id": identifier, "action": "RETAIN", "guard": None}
        created = _timestamp(record.get("created_at"))
        consistent = all(
            record.get(flag) is True
            for flag in (
                "object_exists",
                "manifest_exists",
                "checksum_verified",
                "database_reference_consistent",
            )
        )
        if not consistent:
            item["guard"] = "archive-object-manifest-database-inconsistent"
        elif record.get("active_rehydration") is True:
            item["guard"] = "active-rehydration"
        elif created < cutoff.astimezone(UTC):
            item["action"] = "ELIGIBLE"
            item["guard"] = "consistent-and-no-active-rehydration"
        else:
            item["guard"] = "archive-not-old-enough"
        output.append(item)
    return {
        "kind": "archive",
        "cutoff": cutoff.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "records": output,
        "safe_to_apply": all(
            item["guard"] != "archive-object-manifest-database-inconsistent"
            for item in output
        ),
    }

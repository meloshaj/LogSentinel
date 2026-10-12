from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from backend.app.retention.engine import (
    KNOWN_STORAGE_CLASSES,
    PolicyError,
    load_policy,
    plan_archive_objects,
    plan_logical_backups,
    plan_pitr_bases,
    plan_wal_objects,
    policy_status,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config" / "retention-policy.yml"
UTC = dt.timezone.utc


def test_canonical_policy_covers_every_known_class_and_is_plan_only() -> None:
    policy = load_policy(POLICY)
    assert set(policy["storage_classes"]) == set(KNOWN_STORAGE_CLASSES)
    status = policy_status(policy)
    assert status["approval_status"] == "PENDING_OPERATOR_APPROVAL"
    assert status["mode"] == "PLAN-ONLY"
    assert status["destructive_apply_enabled"] is False


def test_unknown_storage_class_fails_closed(tmp_path: Path) -> None:
    import yaml

    value = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    value["storage_classes"]["postgres.unknown"] = value["storage_classes"]["postgres.raw_logs"]
    target = tmp_path / "bad.yml"
    target.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(PolicyError, match="unknown storage class"):
        load_policy(target)


def test_logical_backups_keep_newest_and_minimum_valid_points() -> None:
    base = dt.datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        {
            "id": f"b-{index}",
            "created_at": (base + dt.timedelta(days=index)).isoformat(),
            "valid": True,
            "manifest_valid": True,
            "checksum_verified": True,
            "remote_verified": True,
        }
        for index in range(4)
    ]
    records.append(
        {
            "id": "invalid-newer",
            "created_at": (base + dt.timedelta(days=20)).isoformat(),
            "valid": False,
            "manifest_valid": False,
            "checksum_verified": False,
            "remote_verified": False,
        }
    )
    plan = plan_logical_backups(
        records,
        cutoff=base + dt.timedelta(days=10),
        minimum_valid_points=3,
    )
    assert plan["newest_valid_id"] == "b-3"
    assert any(item["id"] == "b-0" and item["action"] == "ELIGIBLE" for item in plan["records"])
    assert all(item["action"] != "ELIGIBLE" for item in plan["records"] if item["id"] in {"b-1", "b-2", "b-3", "invalid-newer"})


def test_pitr_and_wal_chain_guard_protects_required_wal() -> None:
    base = dt.datetime(2025, 1, 1, tzinfo=UTC)
    bases = [
        {
            "id": "base-old",
            "created_at": base.isoformat(),
            "valid": True,
            "manifest_valid": True,
            "independently_usable": True,
        },
        {
            "id": "base-current",
            "created_at": (base + dt.timedelta(days=100)).isoformat(),
            "valid": True,
            "manifest_valid": True,
            "independently_usable": True,
        },
    ]
    base_plan = plan_pitr_bases(
        bases,
        cutoff=base + dt.timedelta(days=50),
        minimum_valid_bases=1,
    )
    assert base_plan["newest_usable_id"] == "base-current"
    wal_plan = plan_wal_objects(
        [
            {"id": "wal-required", "required_by_base_ids": ["base-current"]},
            {"id": "wal-redundant", "required_by_base_ids": ["base-old"]},
            {"id": "wal-unknown"},
        ],
        retained_base_ids=set(base_plan["retained_base_ids"]),
    )
    by_id = {item["id"]: item for item in wal_plan["records"]}
    assert by_id["wal-required"]["action"] == "RETAIN"
    assert by_id["wal-redundant"]["action"] == "ELIGIBLE"
    assert by_id["wal-unknown"]["guard"] == "unknown-chain-dependency"
    assert wal_plan["safe_to_apply"] is False


def test_archive_inconsistency_blocks_object_deletion() -> None:
    now = dt.datetime(2026, 9, 18, tzinfo=UTC)
    plan = plan_archive_objects(
        [
            {
                "id": "bad",
                "created_at": "2026-01-01T00:00:00Z",
                "object_exists": False,
                "manifest_exists": True,
                "checksum_verified": True,
                "database_reference_consistent": False,
            },
            {
                "id": "good",
                "created_at": "2026-01-01T00:00:00Z",
                "object_exists": True,
                "manifest_exists": True,
                "checksum_verified": True,
                "database_reference_consistent": True,
                "active_rehydration": False,
            },
        ],
        cutoff=now,
    )
    by_id = {item["id"]: item for item in plan["records"]}
    assert by_id["bad"]["action"] == "RETAIN"
    assert by_id["good"]["action"] == "ELIGIBLE"
    assert plan["safe_to_apply"] is False


def test_no_legacy_unbounded_cleanup_delete_remains() -> None:
    cleanup = (ROOT / "backend/app/cli/cleanup_storage.py").read_text(encoding="utf-8")
    archive = (ROOT / "backend/app/archive/worker.py").read_text(encoding="utf-8")
    assert "DELETE FROM anomaly_events" not in cleanup
    assert "_purge_expired_owned_data" not in archive

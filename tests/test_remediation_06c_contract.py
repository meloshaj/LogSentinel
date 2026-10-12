from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_representative_fixture_manifest_covers_required_boundaries() -> None:
    payload = json.loads(
        (ROOT / "scripts/integration/remediation_06c_fixtures.json").read_text(
            encoding="utf-8"
        )
    )
    fixture_ids = {fixture["id"] for fixture in payload["fixtures"]}
    assert {
        "pre-distributed-correctness",
        "pre-durable-webhook",
        "pre-release-gates",
        "pre-tenant-authority",
        "pre-pipeline-durability",
        "pre-per-user-ownership",
    } <= fixture_ids
    assert "pre-tenant-partitioning" in {
        family["family"] for family in payload["historical_bootstrap_families"]
    }


def test_migration_lifecycle_has_opt_in_interruption_hooks_and_gap_guard() -> None:
    source = (ROOT / "scripts/database_lifecycle.py").read_text(encoding="utf-8")
    assert "migration ledger gap" in source
    assert "LOGSENTINEL_ALLOW_TEST_HOOKS" in source
    assert "LOGSENTINEL_TEST_INTERRUPT_VERSION" in source


def test_backup_scheduler_is_exact_profile_and_requires_schedule_approval() -> None:
    service = (ROOT / "deploy/systemd/logsentinel-backup.service").read_text(
        encoding="utf-8"
    )
    timer = (ROOT / "deploy/systemd/logsentinel-backup.timer").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "scripts/install_backup_scheduler.sh").read_text(
        encoding="utf-8"
    )
    assert "Environment=COMPOSE_PROFILES=backup" in service
    assert "run --rm --no-deps backup" in service
    assert "OnCalendar=*-*-* 02:00:00 UTC" in timer
    assert "RandomizedDelaySec=15min" in timer
    assert "Persistent=true" in timer
    assert "LOGSENTINEL_BACKUP_SCHEDULE_APPROVED" in installer


def test_backup_status_reports_only_checksum_valid_artifacts(tmp_path: Path) -> None:
    dump = tmp_path / "logsentinel_test_20260916T000000Z.dump"
    dump.write_bytes(b"disposable backup proof")
    checksum = hashlib.sha256(dump.read_bytes()).hexdigest()
    dump.with_name(dump.name + ".sha256").write_text(
        f"{checksum}  {dump.name}\n", encoding="utf-8"
    )
    dump.with_name(dump.name + ".manifest.json").write_text(
        json.dumps(
            {
                "dump_filename": dump.name,
                "backup_filename": dump.name,
                "dump_sha256": checksum,
                "created_at_utc": "2026-09-16T00:00:00Z",
                "migration_head": "20260913_0010_per_user_data_ownership",
                "server_postgresql_major": 16,
                "pg_dump_major": 16,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "scripts/backup_status.py", "--directory", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["valid_backup_count"] == 1
    assert report["last_successful_backup"]["dump_filename"] == dump.name


def test_backup_status_returns_nonzero_without_a_valid_backup(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/backup_status.py", "--directory", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert report["status"] == "FAIL"
    assert report["last_successful_backup"] is None


def test_backup_status_ignores_malformed_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "legacy.dump.manifest.json"
    manifest.write_text(json.dumps({"created_at_utc": "2026-09-16T00:00:00Z"}))
    result = subprocess.run(
        [sys.executable, "scripts/backup_status.py", "--directory", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert report["observed_manifest_count"] == 1
    assert report["last_successful_backup"] is None

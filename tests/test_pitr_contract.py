from __future__ import annotations

import json
from pathlib import Path

from scripts.migration_preflight import (
    RESTORE_FORWARD_FIX,
    SAFE_STOP,
    SUPPORTED_AUTOMATIC,
    SUPPORTED_LEGACY,
    UNSUPPORTED,
    classify_schema,
)
from scripts.physical_base_backup import _major


ROOT = Path(__file__).resolve().parents[1]


def test_production_compose_contracts_pitr_without_durability_weakening() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "docker/postgres/Dockerfile" in compose
    assert "archive_mode=on" in compose
    assert "wal_level=replica" in compose
    assert "archive_timeout=300s" in compose
    assert "archive_command=/usr/local/bin/logsentinel-wal-archive.sh" in compose
    assert "max_wal_size=1GB" in compose
    assert "max_wal_senders=2" in compose
    assert "hba_file=/etc/postgresql/pg_hba.conf" in compose
    assert "pg_hba.conf:/etc/postgresql/pg_hba.conf:ro" in compose
    assert "pitr-base-backup" in compose
    assert "synchronous_commit=off" not in compose
    assert "fsync=off" not in compose
    hba = (ROOT / "docker/postgres/pg_hba.conf").read_text(encoding="utf-8")
    assert "host    replication     logsentinel     172.18.0.0/16" in hba


def test_pitr_image_contains_only_repository_helpers_and_no_credentials() -> None:
    dockerfile = (ROOT / "docker/postgres/Dockerfile").read_text(encoding="utf-8")
    assert "FROM timescale/timescaledb@sha256:4e459e217f00cbb09920c34d245501e63427e6767a495de57ce76823ff280f12" in dockerfile
    assert "timescale/timescaledb:2.17.2-pg16" in dockerfile
    assert "py3-boto3" in dockerfile
    assert "scripts/wal_archive.py" in dockerfile
    assert "scripts/wal_restore.py" in dockerfile
    assert "S3_SECRET_ACCESS_KEY=" not in dockerfile


def test_physical_base_backup_contract_is_manifest_last_and_pg16_compatible() -> None:
    source = (ROOT / "scripts/physical_base_backup.py").read_text(encoding="utf-8")
    assert "pg_basebackup" in source
    assert '"--wal-method",' in source
    assert '"stream",' in source
    assert '"--manifest-checksums=SHA256",' in source
    assert "upload_file_verified" in source
    assert "recovery-manifest.json" in source
    assert "expected_keys <= observed_keys" in source
    assert _major("pg_basebackup (PostgreSQL) 16.15 (Debian)") == 16


def test_migration_detector_categories_are_fail_closed() -> None:
    assert (
        classify_schema({"has_application_tables": False})["classification"]
        == SUPPORTED_AUTOMATIC
    )
    assert (
        classify_schema(
            {
                "has_application_tables": True,
                "has_canonical_marker": True,
                "has_owner_columns": True,
            }
        )["classification"]
        == SUPPORTED_AUTOMATIC
    )
    assert (
        classify_schema(
            {"has_application_tables": True, "has_post_tenant_shape": True}
        )["classification"]
        == SUPPORTED_LEGACY
    )
    assert (
        classify_schema({"has_application_tables": True, "has_pre_tenant_shape": True})[
            "classification"
        ]
        == SAFE_STOP
    )
    assert (
        classify_schema(
            {"has_application_tables": True, "has_schema_migrations": True}
        )["classification"]
        == RESTORE_FORWARD_FIX
    )
    assert (
        classify_schema({"has_application_tables": True})["classification"]
        == UNSUPPORTED
    )


def test_migration_detector_contract_has_no_mutation_commands() -> None:
    source = (ROOT / "scripts/migration_preflight.py").read_text(encoding="utf-8")
    assert "UPDATE " not in source
    assert "DELETE " not in source
    assert "DROP " not in source
    assert "CREATE " not in source
    assert "INSERT " not in source
    assert json.loads((ROOT / "scripts/migration_manifest.json").read_text())[
        "active_migrations"
    ]

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import copy
import os
from pathlib import Path

import asyncpg
import pytest
import yaml

from backend.app.retention.engine import load_policy
from scripts import retention


DSN = os.getenv("RETENTION_INTEGRATION_DSN")


@pytest.mark.skipif(not DSN, reason="requires disposable PostgreSQL DSN")
def test_bounded_retention_apply_with_fk_order_and_retry(tmp_path: Path) -> None:
    asyncio.run(_run(tmp_path, DSN))


async def _run(tmp_path: Path, dsn: str | None) -> None:
    assert dsn
    connection = await asyncpg.connect(dsn)
    try:
        schema = """
            CREATE TABLE feature_windows (
                id serial PRIMARY KEY,
                tenant_id text NOT NULL, owner_user_id bigint NOT NULL, window_id text NOT NULL,
                created_at timestamptz NOT NULL, UNIQUE (tenant_id, owner_user_id, window_id)
            );
            CREATE TABLE anomaly_events (
                id serial PRIMARY KEY, tenant_id text NOT NULL, owner_user_id bigint NOT NULL,
                window_id text NOT NULL, created_at timestamptz NOT NULL,
                FOREIGN KEY (tenant_id, owner_user_id, window_id)
                  REFERENCES feature_windows(tenant_id, owner_user_id, window_id) ON DELETE CASCADE
            );
            CREATE TABLE tracking_loops (
                id serial PRIMARY KEY, tenant_id text NOT NULL, owner_user_id bigint NOT NULL,
                window_id text NOT NULL, created_at timestamptz NOT NULL,
                FOREIGN KEY (tenant_id, owner_user_id, window_id)
                  REFERENCES feature_windows(tenant_id, owner_user_id, window_id) ON DELETE CASCADE
            );
            CREATE TABLE incidents (
                id serial PRIMARY KEY, tenant_id text NOT NULL, owner_user_id bigint NOT NULL,
                status text NOT NULL, created_at timestamptz NOT NULL
            );
            CREATE TABLE pipeline_feature_inputs (
                tenant_id text NOT NULL, owner_user_id bigint NOT NULL, event_id text NOT NULL,
                created_at timestamptz NOT NULL, PRIMARY KEY (tenant_id, owner_user_id, event_id)
            );
            CREATE TABLE pipeline_outbox (
                id text PRIMARY KEY, tenant_id text NOT NULL, owner_user_id bigint,
                status text NOT NULL, lease_expires_at timestamptz, created_at timestamptz NOT NULL
            );
            CREATE TABLE pipeline_ledger (
                tenant_id text NOT NULL, owner_user_id bigint NOT NULL, stage text NOT NULL,
                event_id text NOT NULL, created_at timestamptz NOT NULL,
                PRIMARY KEY (tenant_id, owner_user_id, stage, event_id)
            );
            CREATE TABLE email_outbox (
                id text PRIMARY KEY, tenant_id text, user_id bigint, status text NOT NULL,
                lease_expires_at timestamptz, created_at timestamptz NOT NULL
            );
            CREATE TABLE logs (
                tenant_id text NOT NULL, owner_user_id bigint NOT NULL, id text NOT NULL,
                ingested_at timestamptz NOT NULL
            );
            """
        for statement in schema.split(";\n"):
            if statement.strip():
                await connection.execute(statement)
        inserts = """
            INSERT INTO feature_windows (tenant_id, owner_user_id, window_id, created_at) VALUES ('tenant-a', 1, 'old-window', TIMESTAMPTZ '2025-01-01T00:00:00+00:00');
            INSERT INTO anomaly_events (tenant_id, owner_user_id, window_id, created_at) VALUES ('tenant-a', 1, 'old-window', TIMESTAMPTZ '2025-01-01T00:00:00+00:00');
            INSERT INTO tracking_loops (tenant_id, owner_user_id, window_id, created_at) VALUES ('tenant-a', 1, 'old-window', TIMESTAMPTZ '2025-01-01T00:00:00+00:00');
            INSERT INTO incidents (tenant_id, owner_user_id, status, created_at) VALUES ('tenant-a', 1, 'RESOLVED', TIMESTAMPTZ '2025-01-01T00:00:00+00:00');
            INSERT INTO pipeline_feature_inputs VALUES ('tenant-a', 1, 'old-event', TIMESTAMPTZ '2025-01-01T00:00:00+00:00');
            INSERT INTO pipeline_outbox VALUES ('old-outbox', 'tenant-a', 1, 'delivered', NULL, TIMESTAMPTZ '2025-01-01T00:00:00+00:00');
            INSERT INTO pipeline_ledger VALUES ('tenant-a', 1, 'feature', 'old-event', TIMESTAMPTZ '2025-01-01T00:00:00+00:00');
            INSERT INTO email_outbox VALUES ('old-email', 'tenant-a', 1, 'delivered', NULL, TIMESTAMPTZ '2025-01-01T00:00:00+00:00');
            INSERT INTO logs VALUES ('tenant-a', 1, 'old-log', TIMESTAMPTZ '2025-01-01T00:00:00+00:00');
            INSERT INTO logs VALUES ('tenant-b', 2, 'new-log', TIMESTAMPTZ '2026-09-18T00:00:00+00:00');
            INSERT INTO feature_windows (tenant_id, owner_user_id, window_id, created_at) VALUES ('tenant-b', 2, 'new-window', TIMESTAMPTZ '2026-09-18T00:00:00+00:00');
        """
        for statement in inserts.split(";\n"):
            if statement.strip():
                await connection.execute(statement)
    finally:
        await connection.close()

    policy = copy.deepcopy(load_policy())
    policy["approval"] = {
        "status": "approved",
        "marker": "fixture-approval",
        "reference": "fixture",
        "approved_at": "2026-09-18T00:00:00Z",
    }
    policy["effective_date"] = "2026-09-18"
    policy["mode"]["dry_run"] = False
    policy["mode"]["destructive_apply_enabled"] = True
    for definition in policy["storage_classes"].values():
        definition["dry_run"] = False
        definition["legal_hold_behavior"] = "none"
    policy_path = tmp_path / "approved-fixture.yml"
    policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    os.environ["RETENTION_DATABASE_URL"] = dsn
    payload = await retention.run(
        argparse.Namespace(policy=str(policy_path), apply=True, no_database=False)
    )
    assert payload["mode"] == "APPLY"
    assert payload["plan"]["applied_rows"]["postgres.feature_windows"] == 1
    assert payload["plan"]["applied_rows"]["postgres.raw_logs"] == 1

    connection = await asyncpg.connect(dsn)
    try:
        assert await connection.fetchval("SELECT COUNT(*) FROM feature_windows") == 1
        assert await connection.fetchval("SELECT COUNT(*) FROM anomaly_events") == 0
        assert await connection.fetchval("SELECT COUNT(*) FROM logs") == 1
        # A retry after committed batches is safe and has no eligible rows.
        payload = await retention.run(
            argparse.Namespace(policy=str(policy_path), apply=True, no_database=False)
        )
        assert all(value == 0 for value in payload["plan"]["applied_rows"].values())
    finally:
        await connection.close()

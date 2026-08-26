import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    REPO_ROOT / "scripts" / "migrations" / "20260826_0001_multitenant_partitioning.sql"
)
MIGRATION_SQL = MIGRATION_PATH.read_text(encoding="utf-8")


def test_logs_primary_key_includes_tenant_and_ingested_at():
    """Assert logs primary key constraint matches (ingested_at, tenant_id, id)."""
    match = re.search(
        r"CREATE TABLE[\s\S]*?logs\s*\([\s\S]*?PRIMARY KEY\s*\((.*?)\)",
        MIGRATION_SQL,
        re.IGNORECASE,
    )
    assert match is not None, "Could not find PRIMARY KEY in logs table"
    pk_cols = [c.strip() for c in match.group(1).split(",")]
    assert "ingested_at" in pk_cols
    assert "id" in pk_cols
    assert "tenant_id" in pk_cols


def test_logs_hypertable_chunk_interval():
    """Assert logs hypertable chunk interval is set to 1 day on ingested_at."""
    match = re.search(
        r"create_hypertable\('logs',\s*'ingested_at',\s*chunk_time_interval\s*=>\s*INTERVAL\s*'1 day'",
        MIGRATION_SQL,
        re.IGNORECASE,
    )
    assert match is not None, (
        "Could not find create_hypertable with chunk_time_interval 1 day on ingested_at"
    )


def test_logs_rollup_1m_cagg_group_by():
    """Assert logs_rollup_1m continuous aggregate definition includes tenant_id in its GROUP BY clause."""
    # Find GROUP BY after CREATE MATERIALIZED VIEW logs_rollup_1m
    parts = MIGRATION_SQL.split("CREATE MATERIALIZED VIEW logs_rollup_1m")
    assert len(parts) > 1
    cagg_def = parts[1].split("WITH NO DATA")[0]

    group_by_match = re.search(r"GROUP BY([\s\S]+)", cagg_def, re.IGNORECASE)
    assert group_by_match is not None, "Could not find GROUP BY in logs_rollup_1m"
    group_by = group_by_match.group(1)
    assert "tenant_id" in group_by.lower()

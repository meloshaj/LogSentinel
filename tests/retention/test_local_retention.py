from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from scripts.local_retention import plan_local


def test_local_plan_preserves_two_newest_and_holds(tmp_path: Path) -> None:
    rollback = tmp_path / "rollback"
    staging = tmp_path / "staging"
    rollback.mkdir()
    staging.mkdir()
    now = dt.datetime(2026, 9, 18, tzinfo=dt.timezone.utc)
    entries = [
        ("release-active", 1),
        ("release-previous", 2),
        ("release-old", 50),
        ("release-held", 60),
    ]
    for name, age_days in entries:
        path = rollback / name
        path.mkdir()
        timestamp = (now - dt.timedelta(days=age_days)).timestamp()
        os.utime(path, (timestamp, timestamp))
    (rollback / "release-held" / ".incident-hold").touch()
    for name, age_days in entries:
        path = rollback / name
        timestamp = (now - dt.timedelta(days=age_days)).timestamp()
        os.utime(path, (timestamp, timestamp))
    (staging / "logsentinel-api-staging-old").mkdir()
    (staging / "logsentinel-api-staging-new").mkdir()
    plan = plan_local(
        rollback,
        staging,
        rollback_age=dt.timedelta(days=30),
        staging_age=dt.timedelta(days=7),
        minimum_snapshots=2,
        active_paths={(rollback / "release-active").resolve()},
        referenced_paths=set(),
        now=now,
    )
    records = {record["name"]: record for record in plan["records"]}
    assert records["release-old"]["action"] == "ELIGIBLE"
    assert (
        records["release-previous"]["blocked_by_guard"]
        == "active-or-previous-known-good-position"
    )
    assert (
        records["release-active"]["blocked_by_guard"]
        == "active-or-previous-known-good-position"
    )
    assert records["release-held"]["blocked_by_guard"] == "incident-or-retention-hold"


def test_local_plan_ignores_untrusted_staging_names(tmp_path: Path) -> None:
    rollback = tmp_path / "rollback"
    staging = tmp_path / "staging"
    rollback.mkdir()
    staging.mkdir()
    (staging / "remove-me").mkdir()
    (staging / "logsentinel-web-staging-old").mkdir()
    plan = plan_local(
        rollback,
        staging,
        rollback_age=dt.timedelta(days=30),
        staging_age=dt.timedelta(days=7),
        minimum_snapshots=2,
        active_paths=set(),
        referenced_paths=set(),
        now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=8),
    )
    assert [record["name"] for record in plan["records"]] == [
        "logsentinel-web-staging-old"
    ]

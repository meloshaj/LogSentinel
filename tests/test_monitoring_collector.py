from __future__ import annotations

from pathlib import Path

from deploy.monitoring.alert_relay import build_message, validate_destination
from scripts.monitoring_collector import (
    ContainerObservation,
    atomic_write,
    parse_timestamp,
    render_metrics,
)


def _metrics(*, backend: ContainerObservation) -> str:
    containers = {
        "postgres": ContainerObservation(1, 1, 0),
        "valkey": ContainerObservation(1, 1, 0),
        "backend": backend,
        "pipeline-worker": ContainerObservation(1, 1, 0),
        "webhook-worker": ContainerObservation(1, 1, 0),
        "archive-worker": ContainerObservation(1, 1, 0),
        "caddy": ContainerObservation(1, 1, 0),
    }
    return render_metrics(
        containers=containers,
        backup={"status": 1.0, "timestamp": 1_700_000_000.0},
        backup_timer_enabled=1,
        backup_timer_active=1,
        backup_service_result=1.0,
        archiver={
            "available": 1.0,
            "archive_mode": 1.0,
            "failed_count": 45.0,
            "last_archived": 1_700_000_000.0,
            "last_failed": 1_699_000_000.0,
        },
        remote_wal={"status": 1.0, "timestamp": 1_700_000_000.0},
        host={
            "disk_size": 100.0,
            "disk_available": 50.0,
            "memory_total": 100.0,
            "memory_available": 50.0,
            "load1": 0.5,
        },
        now=1_700_000_100.0,
        canary=0,
    )


def test_collector_renders_healthy_baseline_and_historical_archiver_count() -> None:
    output = _metrics(backend=ContainerObservation(1, 1, 0))

    assert 'logsentinel_container_health{service="backend"} 1' in output
    assert "logsentinel_pg_archiver_failed_total 45.0" in output
    assert "logsentinel_monitoring_canary 0" in output


def test_collector_renders_down_service_and_restart_count_without_fabrication() -> None:
    output = _metrics(backend=ContainerObservation(0, 0, 3))

    assert 'logsentinel_container_running{service="backend"} 0' in output
    assert 'logsentinel_container_health{service="backend"} 0' in output
    assert 'logsentinel_container_restarts_total{service="backend"} 3' in output


def test_collector_timestamp_parser_fails_closed() -> None:
    assert parse_timestamp("2026-09-17T19:00:00Z") > 0
    assert parse_timestamp("not-a-timestamp") == 0
    assert parse_timestamp(None) == 0


def test_textfile_write_is_atomic_and_creates_parent(tmp_path: Path) -> None:
    destination = tmp_path / "textfile" / "ops.prom"

    atomic_write(destination, "metric 1\n")

    assert destination.read_text(encoding="utf-8") == "metric 1\n"
    assert not list(destination.parent.glob(".*.ops.prom.*"))


def test_alert_relay_requires_approved_https_discord_destination() -> None:
    assert validate_destination("https://discord.com/api/webhooks/id/token")

    for value in (
        "http://discord.com/api/webhooks/id/token",
        "https://example.invalid/api/webhooks/id/token",
        "https://user:password@discord.com/api/webhooks/id/token",
    ):
        try:
            validate_destination(value)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe operator destination accepted")


def test_alert_relay_message_is_bounded_and_control_safe() -> None:
    message = build_message(
        {
            "status": "firing",
            "alerts": [
                {
                    "labels": {
                        "alertname": "LogSentinelMonitoringCanary",
                        "severity": "warning",
                        "component": "monitoring-canary",
                    },
                    "annotations": {
                        "summary": "canary\nsummary",
                        "description": "safe detail",
                    },
                }
            ],
        }
    )

    assert "\n" in message
    assert "canary summary" in message
    assert len(message) <= 1_800

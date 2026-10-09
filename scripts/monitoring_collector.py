"""Collect bounded host and recovery signals for the Prometheus textfile collector.

This collector is intentionally read-only with respect to LogSentinel state. It
inspects Docker health/restart state, systemd backup state, local backup
manifests, PostgreSQL archiver metadata, remote WAL metadata, and host capacity.
It writes textfile metrics atomically and never writes credentials or raw helper
output to the metric files.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_CONTAINERS = {
    "postgres": "logsentinel-timescaledb-1",
    "valkey": "logsentinel_valkey_prod",
    "backend": "logsentinel-backend-1",
    "pipeline-worker": "logsentinel-pipeline-worker-1",
    "webhook-worker": "logsentinel-webhook-worker-1",
    "archive-worker": "logsentinel-archive-worker-1",
    "caddy": "logsentinel-caddy-1",
}

BACKUP_SCRIPT = Path("/home/ubuntu/LogSentinel/scripts/backup_status.py")
BACKUP_DIRECTORY = Path("/home/ubuntu/logsentinel-database-backups")
POSTGRES_CONTAINER = EXPECTED_CONTAINERS["postgres"]
WAL_STATUS_PATH = "/usr/local/lib/logsentinel/wal_archive_status.py"


class CollectorUnavailable(RuntimeError):
    """Raised when the collector itself cannot obtain a safe observation."""


@dataclass(frozen=True)
class ContainerObservation:
    running: int
    healthy: int
    restart_count: int


def _run(
    args: list[str], *, timeout: float = 10.0, check: bool = False
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CollectorUnavailable(f"required command unavailable: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectorUnavailable(f"command timed out: {args[0]}") from exc
    if check and result.returncode != 0:
        raise CollectorUnavailable(f"command failed: {args[0]}")
    return result


def parse_timestamp(value: Any) -> float:
    """Parse a bounded ISO timestamp without exposing the source value."""
    if not isinstance(value, str) or not value.strip():
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, parsed.timestamp())


def inspect_container(name: str) -> ContainerObservation:
    """Return safe state for one expected container; absent means down."""
    result = _run(["docker", "inspect", name], timeout=8.0)
    if result.returncode != 0:
        return ContainerObservation(running=0, healthy=0, restart_count=0)
    try:
        payload = json.loads(result.stdout)
        state = payload[0]["State"]
        running = 1 if state.get("Running") is True else 0
        health = state.get("Health", {}).get("Status")
        healthy = 1 if health == "healthy" else 0
        restart_count = int(payload[0].get("RestartCount", 0))
    except (ValueError, KeyError, IndexError, TypeError):
        return ContainerObservation(running=0, healthy=0, restart_count=0)
    return ContainerObservation(
        running=running,
        healthy=healthy,
        restart_count=max(0, restart_count),
    )


def _systemd_property(unit: str, property_name: str) -> str:
    result = _run(
        ["systemctl", "show", unit, f"--property={property_name}", "--value"],
        timeout=5.0,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def systemd_active(unit: str) -> int:
    result = _run(["systemctl", "is-active", unit], timeout=5.0)
    return 1 if result.returncode == 0 and result.stdout.strip() == "active" else 0


def systemd_enabled(unit: str) -> int:
    result = _run(["systemctl", "is-enabled", unit], timeout=5.0)
    return 1 if result.returncode == 0 and result.stdout.strip() == "enabled" else 0


def backup_observation() -> dict[str, float]:
    """Read only checksum-valid local backup metadata."""
    result = _run(
        ["python3", str(BACKUP_SCRIPT), "--directory", str(BACKUP_DIRECTORY)],
        timeout=25.0,
    )
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        payload = {}
    last = payload.get("last_successful_backup")
    return {
        "status": 1.0 if payload.get("status") == "PASS" else 0.0,
        "timestamp": parse_timestamp(last.get("created_at_utc"))
        if isinstance(last, dict)
        else 0.0,
    }


def backup_service_observation() -> float:
    result = _systemd_property("logsentinel-backup.service", "Result")
    status = _systemd_property("logsentinel-backup.service", "ExecMainStatus")
    return 1.0 if result == "success" and status in {"", "0"} else 0.0


def postgres_archiver_observation() -> dict[str, float]:
    query = (
        "SELECT current_setting('archive_mode') || '|' || "
        "COALESCE(EXTRACT(EPOCH FROM last_archived_time)::text, '') || '|' || "
        "COALESCE(EXTRACT(EPOCH FROM last_failed_time)::text, '') || '|' || "
        "failed_count FROM pg_stat_archiver;"
    )
    result = _run(
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "psql",
            "-U",
            "logsentinel",
            "-d",
            "logsentinel_db",
            "-Atq",
            "-c",
            query,
        ],
        timeout=10.0,
    )
    if result.returncode != 0:
        return {
            "available": 0.0,
            "archive_mode": 0.0,
            "failed_count": 0.0,
            "last_archived": 0.0,
            "last_failed": 0.0,
        }
    fields = result.stdout.strip().split("|", 3)
    if len(fields) != 4:
        return {
            "available": 0.0,
            "archive_mode": 0.0,
            "failed_count": 0.0,
            "last_archived": 0.0,
            "last_failed": 0.0,
        }
    try:
        return {
            "available": 1.0,
            "archive_mode": 1.0 if fields[0].lower() == "on" else 0.0,
            "failed_count": max(0.0, float(fields[3] or 0)),
            "last_archived": max(0.0, float(fields[1] or 0)),
            "last_failed": max(0.0, float(fields[2] or 0)),
        }
    except ValueError:
        return {
            "available": 0.0,
            "archive_mode": 0.0,
            "failed_count": 0.0,
            "last_archived": 0.0,
            "last_failed": 0.0,
        }


def remote_wal_observation() -> dict[str, float]:
    """Read bounded remote WAL metadata using the existing DB-image helper."""
    result = _run(
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "python3",
            WAL_STATUS_PATH,
            "--limit",
            "1",
        ],
        timeout=20.0,
    )
    try:
        payload = json.loads(result.stdout)
        latest = payload.get("latest")
        return {
            "status": 1.0 if payload.get("status") == "PASS" else 0.0,
            "timestamp": parse_timestamp(latest.get("last_modified"))
            if isinstance(latest, dict)
            else 0.0,
        }
    except (ValueError, TypeError):
        return {"status": 0.0, "timestamp": 0.0}


def host_observation() -> dict[str, float]:
    usage = shutil.disk_usage("/")
    memory: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition(":")
        if name in {"MemTotal", "MemAvailable"}:
            memory[name] = int(value.strip().split()[0]) * 1024
    return {
        "disk_size": float(usage.total),
        "disk_available": float(usage.free),
        "memory_total": float(memory.get("MemTotal", 0)),
        "memory_available": float(memory.get("MemAvailable", 0)),
        "load1": float(os.getloadavg()[0]),
    }


def read_canary(path: Path) -> int:
    try:
        return 1 if path.read_text(encoding="ascii").strip() == "1" else 0
    except (FileNotFoundError, OSError, UnicodeError):
        return 0


def _line(name: str, value: float | int, labels: dict[str, str] | None = None) -> str:
    if labels:
        rendered = ",".join(f'{key}="{value_}"' for key, value_ in labels.items())
        return f"{name}{{{rendered}}} {value}"
    return f"{name} {value}"


def render_metrics(
    *,
    containers: dict[str, ContainerObservation],
    backup: dict[str, float],
    backup_timer_enabled: int,
    backup_timer_active: int,
    backup_service_result: float,
    archiver: dict[str, float],
    remote_wal: dict[str, float],
    host: dict[str, float],
    now: float,
    canary: int,
) -> str:
    lines = [
        "# TYPE logsentinel_container_running gauge",
        "# TYPE logsentinel_container_health gauge",
        "# TYPE logsentinel_container_restarts_total counter",
    ]
    for service, observation in containers.items():
        labels = {"service": service}
        lines.extend(
            (
                _line("logsentinel_container_running", observation.running, labels),
                _line("logsentinel_container_health", observation.healthy, labels),
                _line(
                    "logsentinel_container_restarts_total",
                    observation.restart_count,
                    labels,
                ),
            )
        )
    lines.extend(
        (
            "# TYPE logsentinel_backup_timer_enabled gauge",
            _line("logsentinel_backup_timer_enabled", backup_timer_enabled),
            _line("logsentinel_backup_timer_active", backup_timer_active),
            _line("logsentinel_backup_service_last_result", backup_service_result),
            _line("logsentinel_backup_latest_valid", backup["status"]),
            _line(
                "logsentinel_backup_latest_valid_timestamp_seconds",
                backup["timestamp"],
            ),
            "# TYPE logsentinel_pitr_database_observation_up gauge",
            _line("logsentinel_pitr_database_observation_up", archiver["available"]),
            _line("logsentinel_pitr_archive_mode", archiver["archive_mode"]),
            "# TYPE logsentinel_pg_archiver_failed_total counter",
            _line("logsentinel_pg_archiver_failed_total", archiver["failed_count"]),
            _line(
                "logsentinel_pg_archiver_last_archived_timestamp_seconds",
                archiver["last_archived"],
            ),
            _line(
                "logsentinel_pg_archiver_last_failed_timestamp_seconds",
                archiver["last_failed"],
            ),
            "# TYPE logsentinel_remote_wal_status gauge",
            _line("logsentinel_remote_wal_status", remote_wal["status"]),
            _line(
                "logsentinel_remote_wal_latest_timestamp_seconds",
                remote_wal["timestamp"],
            ),
            "# TYPE logsentinel_host_disk_size_bytes gauge",
            _line("logsentinel_host_disk_size_bytes", host["disk_size"]),
            _line("logsentinel_host_disk_available_bytes", host["disk_available"]),
            _line("logsentinel_host_memory_total_bytes", host["memory_total"]),
            _line("logsentinel_host_memory_available_bytes", host["memory_available"]),
            _line("logsentinel_host_load1", host["load1"]),
            _line("logsentinel_monitoring_canary", canary),
            _line("logsentinel_ops_collector_last_success_timestamp_seconds", now),
        )
    )
    return "\n".join(lines) + "\n"


def render_status(*, success: int, now: float) -> str:
    return "\n".join(
        (
            "# TYPE logsentinel_ops_collector_success gauge",
            _line("logsentinel_ops_collector_success", success),
            _line("logsentinel_ops_collector_last_run_timestamp_seconds", now),
            "",
        )
    )


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o644)
    os.replace(temporary, path)


def collect(*, output_dir: Path, canary_file: Path) -> None:
    now = time.time()
    containers = {
        service: inspect_container(name)
        for service, name in EXPECTED_CONTAINERS.items()
    }
    backup = backup_observation()
    archiver = postgres_archiver_observation()
    remote_wal = remote_wal_observation()
    content = render_metrics(
        containers=containers,
        backup=backup,
        backup_timer_enabled=systemd_enabled("logsentinel-backup.timer"),
        backup_timer_active=systemd_active("logsentinel-backup.timer"),
        backup_service_result=backup_service_observation(),
        archiver=archiver,
        remote_wal=remote_wal,
        host=host_observation(),
        now=now,
        canary=read_canary(canary_file),
    )
    atomic_write(output_dir / "logsentinel_ops.prom", content)
    atomic_write(
        output_dir / "logsentinel_ops_status.prom", render_status(success=1, now=now)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--canary-file", type=Path, required=True)
    args = parser.parse_args(argv)
    now = time.time()
    try:
        collect(output_dir=args.output_dir, canary_file=args.canary_file)
    except (CollectorUnavailable, OSError, ValueError) as exc:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            args.output_dir / "logsentinel_ops_status.prom",
            render_status(success=0, now=now),
        )
        print(f"monitoring collector failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

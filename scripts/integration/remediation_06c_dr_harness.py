"""Disposable remote-backup, PITR, Valkey, and combined-DR rehearsal.

This harness deliberately uses isolated Docker containers and volumes.  It
does not connect to, or mutate, the production host.  The logical backup and
restore steps invoke the repository's approved PG16 scripts inside the same
runner image used by the production backup profile.
"""

# The repository-root path bootstrap must precede imports from the local
# scripts namespace when this file is executed directly.
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import re
import secrets
import shlex
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import asyncpg
import boto3
from redis.asyncio import Redis

from scripts.integration import remediation_06c_migration_harness as migration


EVIDENCE_DIR = REPO_ROOT / "temporary-report" / "remediation-06c" / "evidence"
RUNNER_IMAGE = "logsentinel-06c-runner"
MINIO_IMAGE = "quay.io/minio/minio@sha256:7274c266cc5ec1a4a8925a49d00f249487e426bf73954c992972081972ecfc49"  # RELEASE.2024-05-07T06-41-25Z; multi-arch index
UTC = dt.timezone.utc


class HarnessFailure(RuntimeError):
    """Raised when a disposable DR assertion fails."""


def safe_text(value: str, *secret_values: str) -> str:
    """Return bounded, secret-safe diagnostic text."""

    result = value[-1800:]
    for secret in secret_values:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = re.sub(
        r"(?i)(password|secret|token|access[_-]?key|private[_-]?key)"
        r"(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        result,
    )
    return result


def docker_run(
    args: list[str],
    *,
    timeout: float = 180.0,
    check: bool = True,
    secret_values: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        detail = safe_text(f"{result.stdout}\n{result.stderr}", *secret_values)
        raise HarnessFailure(
            f"docker {' '.join(args[:5])} failed with exit "
            f"{result.returncode}: {detail}"
        )
    return result


def start_postgres(
    config: migration.Config,
    *,
    database: str,
    mounts: list[str] | None = None,
    command: list[str] | None = None,
    entrypoint: str | None = None,
    data_tmpfs: bool = True,
) -> None:
    args = [
        "run",
        "-d",
        "--name",
        config.postgres_container,
        "--shm-size",
        "256m",
        "-e",
        f"POSTGRES_USER={config.postgres_user}",
        "-e",
        f"POSTGRES_PASSWORD={config.postgres_password}",
        "-e",
        f"POSTGRES_DB={database}",
        "-p",
        f"{config.postgres_port}:5432",
    ]
    if data_tmpfs:
        args[4:4] = ["--tmpfs", "/var/lib/postgresql/data"]
    for mount in mounts or []:
        args.extend(["-v", mount])
    if entrypoint:
        args.extend(["--entrypoint", entrypoint])
    args.extend([migration.POSTGRES_IMAGE])
    if command:
        args.extend(command)
    else:
        args.extend(
            [
                "postgres",
                "-c",
                "fsync=on",
                "-c",
                "synchronous_commit=on",
                "-c",
                "full_page_writes=on",
                "-c",
                "max_connections=100",
            ]
        )
    docker_run(
        args,
        timeout=300,
        secret_values=(config.postgres_password,),
    )


def start_valkey(
    *,
    name: str,
    port: int,
    volume: str | None,
    tmpfs: bool = False,
) -> None:
    args = [
        "run",
        "-d",
        "--name",
        name,
    ]
    if volume:
        args.extend(["-v", f"{volume}:/data"])
    if tmpfs:
        args.extend(["--tmpfs", "/data"])
    args.extend(
        [
            "-p",
            f"127.0.0.1:{port}:6379",
            migration.VALKEY_IMAGE,
            "valkey-server",
            "--save",
            "60",
            "1",
            "--appendonly",
            "yes",
            "--appendfsync",
            "everysec",
            "--maxmemory-policy",
            "noeviction",
        ]
    )
    docker_run(args, timeout=120)


async def wait_for_postgres(config: migration.Config, database: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            connection = await asyncpg.connect(**config.db_kwargs(database))
            await connection.close()
            return
        except (OSError, asyncpg.PostgresError):
            await asyncio.sleep(1)
    raise HarnessFailure(f"timed out waiting for PostgreSQL database {database}")


async def wait_for_valkey(port: int) -> None:
    client = Redis.from_url(f"redis://127.0.0.1:{port}/0")
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if await client.ping():
                    return
            except Exception:
                pass
            await asyncio.sleep(1)
    finally:
        await client.aclose()
    raise HarnessFailure("timed out waiting for Valkey")


def remove_container(name: str) -> None:
    docker_run(["rm", "--force", name], timeout=60, check=False)


def remove_volume(name: str) -> None:
    docker_run(["volume", "rm", "--force", name], timeout=60, check=False)


async def seed_current_recovery_data(
    config: migration.Config,
    database: str,
    run_id: str,
) -> dict[str, Any]:
    connection = await migration.connection_for(config, database)
    tenant_id = f"drtenant_{run_id}"
    try:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO tenants (id, name, status)
                VALUES ($1, '06C disposable recovery tenant', 'active')
                ON CONFLICT (id) DO NOTHING
                """,
                tenant_id,
            )
            owner_id = await connection.fetchval(
                """
                INSERT INTO users (
                    email, hashed_password, full_name, organization, tenant_id,
                    status, role, email_verified_at
                ) VALUES ($1, 'disposable-hash', '06C Owner', '06C', $2,
                          'active', 'admin', NOW())
                RETURNING id
                """,
                f"owner-{run_id}@example.test",
                tenant_id,
            )
            viewer_id = await connection.fetchval(
                """
                INSERT INTO users (
                    email, hashed_password, full_name, organization, tenant_id,
                    status, role, email_verified_at
                ) VALUES ($1, 'disposable-hash', '06C Viewer', '06C', $2,
                          'active', 'viewer', NOW())
                RETURNING id
                """,
                f"viewer-{run_id}@example.test",
                tenant_id,
            )
            await connection.executemany(
                """
                INSERT INTO tenant_memberships (tenant_id, user_id, role, status)
                VALUES ($1, $2, $3, 'active')
                """,
                [
                    (tenant_id, owner_id, "admin"),
                    (tenant_id, viewer_id, "viewer"),
                ],
            )
            await connection.execute(
                """
                INSERT INTO logs (
                    id, event_id, tenant_id, owner_user_id, timestamp, service,
                    raw_message, template_id, template_text, parameters, level,
                    source, environment, metadata, created_at, ingested_at
                ) VALUES ('06c-recovery-log-0000001', '06c-recovery-event-0001',
                          $1, $2, NOW(), 'recovery-fixture',
                          'durable recovery fixture', 'recovery-template',
                          'durable recovery fixture', '[]'::jsonb, 'INFO',
                          '06c', 'disposable', '{"fixture": true}'::jsonb,
                          NOW(), NOW())
                ON CONFLICT (tenant_id, ingested_at, id) DO NOTHING
                """,
                tenant_id,
                owner_id,
            )
            await connection.execute(
                """
                INSERT INTO pipeline_ledger
                    (tenant_id, owner_user_id, stage, event_id)
                VALUES ($1, $2, 'raw', '06c-recovery-event-0001')
                ON CONFLICT DO NOTHING
                """,
                tenant_id,
                owner_id,
            )
            await connection.execute(
                """
                INSERT INTO pipeline_outbox (
                    id, tenant_id, owner_user_id, topic, dedup_key, payload,
                    status, event_id
                ) VALUES ('06c-recovery-outbox-0001', $1, $2, 'feature',
                          '06c-recovery-dedup-0001', '{"fixture": true}'::jsonb,
                          'pending', '06c-recovery-event-0001')
                ON CONFLICT (id) DO NOTHING
                """,
                tenant_id,
                owner_id,
            )
            await connection.execute(
                """
                INSERT INTO pipeline_feature_inputs (
                    tenant_id, owner_user_id, event_id, event_timestamp, payload
                ) VALUES ($1, $2, '06c-recovery-event-0001', NOW(),
                          '{"fixture": true}'::jsonb)
                ON CONFLICT DO NOTHING
                """,
                tenant_id,
                owner_id,
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dr_recovery_markers (
                    marker TEXT PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
                    owner_user_id BIGINT NOT NULL REFERENCES users(id),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO dr_recovery_markers (marker, tenant_id, owner_user_id)
                VALUES ('base-recovery-point', $1, $2)
                ON CONFLICT (marker) DO NOTHING
                """,
                tenant_id,
                owner_id,
            )
        return {
            "tenant_id": tenant_id,
            "owner_user_id": int(owner_id),
            "viewer_user_id": int(viewer_id),
            "marker": "base-recovery-point",
        }
    finally:
        await connection.close()


async def update_recovery_marker(
    config: migration.Config, database: str, marker: str
) -> None:
    connection = await migration.connection_for(config, database)
    try:
        await connection.execute(
            "UPDATE dr_recovery_markers SET marker = $1 WHERE marker = $2",
            marker,
            "base-recovery-point",
        )
    finally:
        await connection.close()


def s3_client(endpoint: str, access_key: str, secret_key: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


async def start_minio(
    *, name: str, port: int, access_key: str, secret_key: str, bucket: str
) -> Any:
    kms_secret = "minio-06c-key:" + base64.b64encode(secrets.token_bytes(32)).decode()
    docker_run(
        [
            "run",
            "-d",
            "--name",
            name,
            "--tmpfs",
            "/data",
            "-e",
            f"MINIO_ROOT_USER={access_key}",
            "-e",
            f"MINIO_ROOT_PASSWORD={secret_key}",
            "-e",
            "MINIO_DOMAIN=host.docker.internal",
            "-e",
            f"MINIO_KMS_SECRET_KEY={kms_secret}",
            "-p",
            f"{port}:9000",
            MINIO_IMAGE,
            "server",
            "/data",
            "--console-address",
            ":9001",
        ],
        timeout=120,
        secret_values=(secret_key, kms_secret),
    )
    endpoint = f"http://127.0.0.1:{port}"
    client = s3_client(endpoint, access_key, secret_key)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            client.create_bucket(Bucket=bucket)
            return client
        except Exception:
            try:
                client.head_bucket(Bucket=bucket)
                return client
            except Exception:
                await asyncio.sleep(1)
    raise HarnessFailure("timed out waiting for disposable S3-compatible object store")


def run_backup(
    config: migration.Config,
    *,
    backup_dir: Path,
    minio_port: int,
    bucket: str,
    access_key: str,
    secret_key: str,
) -> dict[str, Any]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    endpoint = f"http://host.docker.internal:{minio_port}"
    mount = f"{backup_dir.resolve()}:/tmp/backups"
    env = [
        "-e",
        "POSTGRES_HOST=host.docker.internal",
        "-e",
        f"POSTGRES_PORT={config.postgres_port}",
        "-e",
        f"POSTGRES_USER={config.postgres_user}",
        "-e",
        f"POSTGRES_PASSWORD={config.postgres_password}",
        "-e",
        f"POSTGRES_DB={config.control_db}",
        "-e",
        "REQUIRE_REMOTE_BACKUP=true",
        "-e",
        f"S3_BUCKET={bucket}",
        "-e",
        "S3_REGION=us-east-1",
        "-e",
        f"S3_ENDPOINT={endpoint}",
        "-e",
        f"S3_ACCESS_KEY_ID={access_key}",
        "-e",
        f"S3_SECRET_ACCESS_KEY={secret_key}",
        "-e",
        "AWS_EC2_METADATA_DISABLED=true",
    ]
    result = docker_run(
        [
            "run",
            "--rm",
            "--user",
            "0:0",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--add-host",
            f"{bucket}.host.docker.internal:host-gateway",
            "-v",
            mount,
            *env,
            RUNNER_IMAGE,
            "bash",
            "/repo/scripts/backup_database.sh",
        ],
        timeout=300,
        check=False,
        secret_values=(
            config.postgres_password,
            access_key,
            secret_key,
        ),
    )
    if result.returncode != 0:
        raise HarnessFailure(
            "approved PG16 backup path failed: "
            + safe_text(
                f"{result.stdout}\n{result.stderr}",
                config.postgres_password,
                access_key,
                secret_key,
            )
        )
    dumps = sorted(backup_dir.glob("*.dump"))
    if not dumps:
        raise HarnessFailure("approved backup path produced no dump artifact")
    dump = dumps[-1]
    manifest = json.loads(
        dump.with_name(dump.name + ".manifest.json").read_text(encoding="utf-8")
    )
    checksum = dump.with_name(dump.name + ".sha256").read_text(encoding="utf-8")
    return {
        "dump_filename": dump.name,
        "dump_size_bytes": dump.stat().st_size,
        "sha256_sidecar": checksum.split()[0],
        "manifest": {
            key: manifest[key]
            for key in (
                "created_at_utc",
                "database_logical_identifier",
                "database_version",
                "server_postgresql_major",
                "pg_dump_major",
                "timescaledb_version",
                "migration_head",
                "backup_filename",
                "dump_sha256",
                "backup_execution_source",
                "backup_execution_version",
                "backup_script_sha256",
            )
            if key in manifest
        },
        "runner_exit_code": result.returncode,
    }


def verify_remote_points(
    client: Any, bucket: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    response = client.list_objects_v2(Bucket=bucket, Prefix="backups/")
    keys = [str(item["Key"]) for item in response.get("Contents", [])]
    dump_keys = sorted(key for key in keys if key.endswith(".dump"))
    points: list[dict[str, Any]] = []
    for dump_key in dump_keys:
        dump_name = dump_key.rsplit("/", 1)[-1]
        checksum_key = dump_key + ".sha256"
        manifest_key = dump_key + ".manifest.json"
        required = {dump_key, checksum_key, manifest_key}
        if not required <= set(keys):
            points.append(
                {
                    "dump_filename": dump_name,
                    "usable": False,
                    "missing": sorted(required - set(keys)),
                }
            )
            continue
        heads = {key: client.head_object(Bucket=bucket, Key=key) for key in required}
        manifest = json.loads(
            client.get_object(Bucket=bucket, Key=manifest_key)["Body"].read()
        )
        checksum_text = (
            client.get_object(Bucket=bucket, Key=checksum_key)["Body"]
            .read()
            .decode("utf-8")
        )
        checksum = checksum_text.split()[0]
        metadata_sha = str(heads[dump_key].get("Metadata", {}).get("sha256", ""))
        usable = bool(
            heads[dump_key].get("ContentLength", 0) > 0
            and heads[checksum_key].get("ContentLength", 0) > 0
            and heads[manifest_key].get("ContentLength", 0) > 0
            and len(checksum) == 64
            and checksum == str(manifest.get("dump_sha256", ""))
            and checksum == metadata_sha
            and manifest.get("dump_filename") == dump_name
        )
        points.append(
            {
                "dump_filename": dump_name,
                "remote_dump_key": dump_key,
                "remote_checksum_key": checksum_key,
                "remote_manifest_key": manifest_key,
                "dump_size_bytes": int(heads[dump_key]["ContentLength"]),
                "checksum": checksum,
                "manifest_created_at_utc": manifest.get("created_at_utc"),
                "remote_metadata_sha256_matches": checksum == metadata_sha,
                "manifest_dump_name_matches": manifest.get("dump_filename")
                == dump_name,
                "usable": usable,
            }
        )
    usable = [point for point in points if point["usable"]]
    return points, {
        "object_count": len(keys),
        "dump_count": len(dump_keys),
        "usable_recovery_point_count": len(usable),
        "at_least_two_usable_points": len(usable) >= 2,
        "selected_dump_filename": usable[-1]["dump_filename"] if usable else None,
    }


def run_restore(
    config: migration.Config,
    *,
    restore_config: migration.Config,
    source_point: dict[str, Any],
    restore_dir: Path,
    minio_port: int,
    bucket: str,
    access_key: str,
    secret_key: str,
    target_db: str,
) -> dict[str, Any]:
    restore_dir.mkdir(parents=True, exist_ok=True)
    endpoint = f"http://host.docker.internal:{minio_port}"
    env = [
        "-e",
        "POSTGRES_HOST=host.docker.internal",
        "-e",
        f"POSTGRES_PORT={restore_config.postgres_port}",
        "-e",
        f"POSTGRES_USER={restore_config.postgres_user}",
        "-e",
        f"POSTGRES_PASSWORD={restore_config.postgres_password}",
        "-e",
        f"POSTGRES_DB={restore_config.control_db}",
        "-e",
        "BACKUP_DIR=/tmp/restore-download",
        "-e",
        f"S3_BUCKET={bucket}",
        "-e",
        "S3_REGION=us-east-1",
        "-e",
        f"S3_ENDPOINT={endpoint}",
        "-e",
        f"S3_ACCESS_KEY_ID={access_key}",
        "-e",
        f"S3_SECRET_ACCESS_KEY={secret_key}",
        "-e",
        "AWS_EC2_METADATA_DISABLED=true",
    ]
    result = docker_run(
        [
            "run",
            "--rm",
            "--user",
            "0:0",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--add-host",
            f"{bucket}.host.docker.internal:host-gateway",
            "-v",
            f"{restore_dir.resolve()}:/tmp/restore-download",
            *env,
            RUNNER_IMAGE,
            "bash",
            "/repo/scripts/restore_database.sh",
            "--source",
            source_point["dump_filename"],
            "--target-db",
            target_db,
            "--confirm-replace",
        ],
        timeout=360,
        check=False,
        secret_values=(
            config.postgres_password,
            access_key,
            secret_key,
        ),
    )
    if result.returncode != 0:
        raise HarnessFailure(
            "approved remote restore path failed: "
            + safe_text(
                f"{result.stdout}\n{result.stderr}",
                config.postgres_password,
                access_key,
                secret_key,
            )
        )
    downloaded = restore_dir / source_point["dump_filename"]
    return {
        "runner_exit_code": result.returncode,
        "downloaded_from_remote": downloaded.exists(),
        "downloaded_dump_size_bytes": downloaded.stat().st_size
        if downloaded.exists()
        else 0,
        "target_database": target_db,
    }


async def valkey_state_rehearsal(
    *,
    config: migration.Config,
    name: str,
    port: int,
    volume: str,
    run_id: str,
) -> dict[str, Any]:
    stream = f"logs:stream:06c:recovery:{run_id}"
    group = "dr_rehearsal_workers"
    consumer = "dr-rehearsal-consumer"
    start_valkey(name=name, port=port, volume=volume)
    await wait_for_valkey(port)
    client = Redis.from_url(f"redis://127.0.0.1:{port}/0", decode_responses=True)
    try:
        first_id = await client.xadd(
            stream,
            {"tenant_id": config.run_id, "owner_user_id": "10001", "kind": "durable"},
        )
        second_id = await client.xadd(
            stream,
            {"tenant_id": config.run_id, "owner_user_id": "10001", "kind": "pending"},
        )
        await client.xgroup_create(stream, group, id="0-0", mkstream=True)
        pending_read = await client.xreadgroup(
            group, consumer, {stream: ">"}, count=1, block=1000
        )
        await client.set(
            f"logsentinel:worker-heartbeat:pipeline:06c-{run_id}",
            "alive",
            ex=120,
        )
        await client.set(
            f"logsentinel:cache:06c-{run_id}",
            "rebuildable-cache",
            ex=120,
        )
        await client.bgrewriteaof()
        await asyncio.sleep(2)
        before = {
            "stream_length": await client.xlen(stream),
            "pending_total": int((await client.xpending(stream, group))["pending"]),
            "first_entry_id": first_id,
            "second_entry_id": second_id,
            "pending_read_count": sum(len(item[1]) for item in pending_read),
            "heartbeat_present": bool(
                await client.exists(
                    f"logsentinel:worker-heartbeat:pipeline:06c-{run_id}"
                )
            ),
            "cache_present": bool(
                await client.exists(f"logsentinel:cache:06c-{run_id}")
            ),
        }
    finally:
        await client.aclose()
    restart_started_at = time.monotonic()
    docker_run(["stop", name], timeout=60, check=False)
    remove_container(name)
    start_valkey(name=name, port=port, volume=volume)
    await wait_for_valkey(port)
    restart_seconds = round(time.monotonic() - restart_started_at, 3)
    restarted = Redis.from_url(f"redis://127.0.0.1:{port}/0", decode_responses=True)
    try:
        pending = await restarted.xpending(stream, group)
        groups = await restarted.xinfo_groups(stream)
        after = {
            "stream_length": await restarted.xlen(stream),
            "pending_total": int(pending["pending"]),
            "heartbeat_present": bool(
                await restarted.exists(
                    f"logsentinel:worker-heartbeat:pipeline:06c-{run_id}"
                )
            ),
            "cache_present": bool(
                await restarted.exists(f"logsentinel:cache:06c-{run_id}")
            ),
            "consumer_group_present": any(
                str(group_item.get("name")) == group for group_item in groups
            ),
        }
    finally:
        await restarted.aclose()
    passed = (
        before["stream_length"] == after["stream_length"] == 2
        and before["pending_total"] == after["pending_total"] == 1
        and after["consumer_group_present"]
        and after["heartbeat_present"]
        and after["cache_present"]
    )
    if not passed:
        raise HarnessFailure(f"Valkey persistence state mismatch: {before} / {after}")
    return {
        "persistence_mode": {
            "appendonly": True,
            "appendfsync": "everysec",
            "rdb_save": "60 1",
            "maxmemory_policy": "noeviction",
        },
        "before_restart": before,
        "after_restart": after,
        "restart_recovery_seconds": restart_seconds,
        "restart_recovery": "PASS",
    }


async def pitr_rehearsal(
    config: migration.Config,
    *,
    work_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    pitr_source = replace(
        config,
        postgres_container=f"logsentinel-06c-pitr-source-{run_id}",
        control_db=f"pitr_{run_id}",
        postgres_port=migration._free_port(),
    )
    pitr_clone = replace(
        pitr_source,
        postgres_container=f"logsentinel-06c-pitr-clone-{run_id}",
        postgres_port=migration._free_port(),
    )
    base_volume = f"logsentinel-06c-pitr-base-{run_id}"
    wal_volume = f"logsentinel-06c-pitr-wal-{run_id}"
    database = pitr_source.control_db
    source_started = False
    clone_started = False
    docker_run(["volume", "create", base_volume], timeout=60)
    docker_run(["volume", "create", wal_volume], timeout=60)
    try:
        startup = (
            "chmod 777 /pitr_base /wal_archive && "
            "exec docker-entrypoint.sh postgres "
            "-c wal_level=replica -c archive_mode=on -c archive_timeout=1s "
            "-c max_wal_senders=2 -c fsync=on -c synchronous_commit=on "
            "-c full_page_writes=on "
            '-c "archive_command=test ! -f /wal_archive/%f && cp %p /wal_archive/%f"'
        )
        start_postgres(
            pitr_source,
            database=database,
            mounts=[f"{base_volume}:/pitr_base", f"{wal_volume}:/wal_archive"],
            command=["-c", startup],
            entrypoint="sh",
        )
        source_started = True
        await wait_for_postgres(pitr_source, database)
        await migration.execute_sql(
            pitr_source, database, migration.INIT_PATH.read_text(encoding="utf-8")
        )
        lifecycle_result = await asyncio.to_thread(
            migration.run_lifecycle, pitr_source, database
        )
        if lifecycle_result["exit_code"] != 0:
            raise HarnessFailure("PITR source current lifecycle failed")
        connection = await migration.connection_for(pitr_source, database)
        try:
            await connection.execute(
                """
                CREATE TABLE pitr_markers (
                    name TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            t1 = await connection.fetchval(
                """
                INSERT INTO pitr_markers (name, created_at)
                VALUES ('T1', clock_timestamp())
                RETURNING created_at
                """
            )
            await connection.fetchval("SELECT pg_switch_wal()")
        finally:
            await connection.close()
        await asyncio.sleep(2)
        base_start = time.monotonic()
        basebackup = docker_run(
            [
                "exec",
                "--user",
                "postgres",
                "-e",
                f"PGPASSWORD={config.postgres_password}",
                pitr_source.postgres_container,
                "pg_basebackup",
                "-h",
                "127.0.0.1",
                "-U",
                config.postgres_user,
                "-D",
                "/pitr_base",
                "-Fp",
                "-X",
                "stream",
                "-c",
                "fast",
            ],
            timeout=300,
            check=False,
            secret_values=(config.postgres_password,),
        )
        if basebackup.returncode != 0:
            raise HarnessFailure(
                "PITR base backup failed: "
                + safe_text(basebackup.stderr, config.postgres_password)
            )
        basebackup_seconds = round(time.monotonic() - base_start, 3)
        connection = await migration.connection_for(pitr_source, database)
        try:
            base_completed = await connection.fetchval("SELECT clock_timestamp()")
            await asyncio.sleep(1.5)
            t2 = await connection.fetchval(
                """
                INSERT INTO pitr_markers (name, created_at)
                VALUES ('T2', clock_timestamp())
                RETURNING created_at
                """
            )
            await connection.fetchval("SELECT pg_switch_wal()")
        finally:
            await connection.close()
        if not (t1 < base_completed < t2):
            raise HarnessFailure(
                f"PITR marker ordering was not usable: {t1}, {base_completed}, {t2}"
            )
        await asyncio.sleep(3)
        target = base_completed + (t2 - base_completed) / 2
        docker_run(["stop", pitr_source.postgres_container], timeout=60, check=False)
        remove_container(pitr_source.postgres_container)
        source_started = False
        recovery_config = (
            "restore_command = 'cp /wal_archive/%f %p'\n"
            f"recovery_target_time = '{target.isoformat()}'\n"
            "recovery_target_action = 'promote'\n"
        )
        config_writer = (
            f"printf %s {shlex.quote(recovery_config)} > "
            "/var/lib/postgresql/data/postgresql.auto.conf && "
            "touch /var/lib/postgresql/data/recovery.signal && "
            "chown postgres:postgres /var/lib/postgresql/data/postgresql.auto.conf "
            "/var/lib/postgresql/data/recovery.signal"
        )
        docker_run(
            [
                "run",
                "--rm",
                "--user",
                "0:0",
                "-v",
                f"{base_volume}:/var/lib/postgresql/data",
                "-v",
                f"{wal_volume}:/wal_archive:ro",
                "--entrypoint",
                "sh",
                migration.POSTGRES_IMAGE,
                "-c",
                config_writer,
            ],
            timeout=120,
        )
        start_postgres(
            pitr_clone,
            database=database,
            mounts=[
                f"{base_volume}:/var/lib/postgresql/data",
                f"{wal_volume}:/wal_archive:ro",
            ],
            command=["postgres"],
            data_tmpfs=False,
        )
        clone_started = True
        recovery_start = time.monotonic()
        await wait_for_postgres(pitr_clone, database)
        promoted = False
        promotion_deadline = time.monotonic() + 120
        while time.monotonic() < promotion_deadline:
            probe = await migration.connection_for(pitr_clone, database)
            try:
                if not await probe.fetchval("SELECT pg_is_in_recovery()"):
                    promoted = True
                    break
            finally:
                await probe.close()
            await asyncio.sleep(1)
        recovery_seconds = round(time.monotonic() - recovery_start, 3)
        connection = await migration.connection_for(pitr_clone, database)
        try:
            names = [
                str(row["name"])
                for row in await connection.fetch(
                    "SELECT name FROM pitr_markers ORDER BY name"
                )
            ]
            in_recovery = bool(await connection.fetchval("SELECT pg_is_in_recovery()"))
            schema = await migration.schema_identity(pitr_clone, database)
            invariants = await migration.invariant_counts(pitr_clone, database)
        finally:
            await connection.close()
        passed = (
            names == ["T1"]
            and promoted
            and not in_recovery
            and schema["migration_ledger"][-1]
            == "20260913_0010_per_user_data_ownership"
            and migration.invariants_passed(invariants)
        )
        if not passed:
            raise HarnessFailure(
                f"PITR target validation failed: markers={names}, "
                f"in_recovery={in_recovery}, invariants={invariants}"
            )
        return {
            "production_state": "NOT CONFIGURED",
            "disposable_configuration": {
                "wal_level": "replica",
                "archive_mode": "on",
                "archive_command": "local disposable WAL volume",
                "archive_timeout": "1s",
                "remote_durable_storage": "disposable named volume only",
            },
            "base_backup_seconds": basebackup_seconds,
            "marker_timestamps": {
                "T1": t1.isoformat(),
                "base_completed": base_completed.isoformat(),
                "T2": t2.isoformat(),
                "target": target.isoformat(),
            },
            "recovery_target": target.isoformat(),
            "recovered_markers": names,
            "post_recovery_in_recovery_mode": in_recovery,
            "recovery_seconds": recovery_seconds,
            "schema": schema,
            "invariants": invariants,
            "result": "PASS",
        }
    finally:
        if source_started:
            remove_container(pitr_source.postgres_container)
        if clone_started:
            remove_container(pitr_clone.postgres_container)
        remove_volume(base_volume)
        remove_volume(wal_volume)


async def run_scenario(config: migration.Config, work_dir: Path) -> dict[str, Any]:
    run_id = config.run_id
    source_db = config.control_db
    minio_name = f"logsentinel-06c-minio-{run_id}"
    minio_port = migration._free_port()
    minio_access = f"minio06c{run_id[:8]}"
    minio_secret = secrets.token_urlsafe(24)
    bucket = f"logsentinel-06c-{run_id}"
    source_started = False
    restore_started = False
    recovery_valkey_name = f"logsentinel-06c-valkey-recovery-{run_id}"
    recovery_valkey_port = migration._free_port()
    recovery_volume = f"logsentinel-06c-valkey-volume-{run_id}"
    clean_valkey_name = f"logsentinel-06c-valkey-clean-{run_id}"
    clean_valkey_port = migration._free_port()
    evidence: dict[str, Any] = {
        "run_id": run_id,
        "status": "PASS",
        "scope": "disposable infrastructure only; production was not contacted",
        "source_identity": migration.source_identity(),
        "production_architecture_observation": {
            "postgresql_pitr": "NOT CONFIGURED in docker-compose.prod.yml/scripts/config/postgresql.conf",
            "valkey_mode": "Compose declares AOF everysec, RDB save 60 1, persistent named volume",
            "production_scheduler": "not verified remotely; no scheduler in docker-compose.prod.yml",
        },
    }
    try:
        start_postgres(config, database=source_db)
        source_started = True
        await wait_for_postgres(config, source_db)
        await migration.execute_sql(
            config, source_db, migration.INIT_PATH.read_text(encoding="utf-8")
        )
        lifecycle_result = await asyncio.to_thread(
            migration.run_lifecycle, config, source_db
        )
        if lifecycle_result["exit_code"] != 0:
            raise HarnessFailure("disposable recovery source lifecycle failed")
        fixture = await seed_current_recovery_data(config, source_db, run_id)
        source_schema = await migration.schema_identity(config, source_db)
        source_invariants = await migration.invariant_counts(config, source_db)
        if not migration.invariants_passed(source_invariants):
            raise HarnessFailure(
                f"source fixture invariants failed: {source_invariants}"
            )
        evidence["source_database"] = {
            "schema": source_schema,
            "invariants": source_invariants,
            "fixture": fixture,
            "lifecycle": lifecycle_result,
        }
        client = await start_minio(
            name=minio_name,
            port=minio_port,
            access_key=minio_access,
            secret_key=minio_secret,
            bucket=bucket,
        )
        first_dir = work_dir / "backup-one"
        first = run_backup(
            config,
            backup_dir=first_dir,
            minio_port=minio_port,
            bucket=bucket,
            access_key=minio_access,
            secret_key=minio_secret,
        )
        await asyncio.sleep(1.5)
        await update_recovery_marker(config, source_db, "second-recovery-point")
        second_dir = work_dir / "backup-two"
        second = run_backup(
            config,
            backup_dir=second_dir,
            minio_port=minio_port,
            bucket=bucket,
            access_key=minio_access,
            secret_key=minio_secret,
        )
        remote_inventory_started = time.monotonic()
        remote_points, remote_summary = verify_remote_points(client, bucket)
        remote_inventory_seconds = round(time.monotonic() - remote_inventory_started, 3)
        if not remote_summary["at_least_two_usable_points"]:
            raise HarnessFailure(
                f"remote recovery-point inventory failed: {remote_summary}"
            )
        selected = remote_points[-1]
        evidence["backup_execution"] = {
            "normal_pg16_backup": "PASS",
            "first_recovery_point": first,
            "second_recovery_point": second,
            "remote_points": remote_points,
            "remote_summary": remote_summary,
            "remote_inventory_seconds": remote_inventory_seconds,
        }
        restore_config = replace(
            config,
            postgres_container=f"logsentinel-06c-restore-pg-{run_id}",
            control_db=f"restore_control_{run_id}",
            postgres_port=migration._free_port(),
        )
        start_postgres(restore_config, database=restore_config.control_db)
        restore_started = True
        await wait_for_postgres(restore_config, restore_config.control_db)
        restore_dir = work_dir / "remote-restore-download"
        target_db = f"restored_{run_id}"
        restore_started_at = time.monotonic()
        restore_runner = run_restore(
            config,
            restore_config=restore_config,
            source_point=selected,
            restore_dir=restore_dir,
            minio_port=minio_port,
            bucket=bucket,
            access_key=minio_access,
            secret_key=minio_secret,
            target_db=target_db,
        )
        restore_seconds = round(time.monotonic() - restore_started_at, 3)
        restored_schema = await migration.schema_identity(restore_config, target_db)
        restored_invariants = await migration.invariant_counts(
            restore_config, target_db
        )
        restored_marker = await migration.connection_for(restore_config, target_db)
        try:
            marker_value = await restored_marker.fetchval(
                "SELECT marker FROM dr_recovery_markers WHERE marker = 'second-recovery-point'"
            )
            restored_row_counts = await migration.row_counts(restore_config, target_db)
        finally:
            await restored_marker.close()
        if (
            not restore_runner["downloaded_from_remote"]
            or restored_schema["migration_ledger"][-1]
            != "20260913_0010_per_user_data_ownership"
            or not migration.invariants_passed(restored_invariants)
            or marker_value != "second-recovery-point"
        ):
            raise HarnessFailure(
                f"remote restore validation failed: schema={restored_schema}, "
                f"invariants={restored_invariants}, marker={marker_value}"
            )
        evidence["remote_restore"] = {
            **restore_runner,
            "selected_recovery_point": selected,
            "elapsed_seconds": restore_seconds,
            "schema": restored_schema,
            "invariants": restored_invariants,
            "row_counts": restored_row_counts,
            "marker_after_restore": marker_value,
            "result": "PASS",
        }
        docker_run(["volume", "create", recovery_volume], timeout=60)
        valkey_result = await valkey_state_rehearsal(
            config=config,
            name=recovery_valkey_name,
            port=recovery_valkey_port,
            volume=recovery_volume,
            run_id=run_id,
        )
        evidence["valkey_persistence_rehearsal"] = valkey_result
        restored_app_config = replace(
            restore_config,
            valkey_container=recovery_valkey_name,
            valkey_port=recovery_valkey_port,
        )
        combined_start = time.monotonic()
        combined_app = await migration.run_application_compatibility(
            restored_app_config,
            target_db,
            work_dir,
            label="combined-dr",
        )
        combined_seconds = round(time.monotonic() - combined_start, 3)
        combined_connection = await migration.connection_for(restore_config, target_db)
        try:
            combined_invariants = await migration.invariant_counts(
                restore_config, target_db
            )
            combined_counts = await migration.row_counts(restore_config, target_db)
        finally:
            await combined_connection.close()
        if not migration.invariants_passed(combined_invariants):
            raise HarnessFailure(
                f"combined recovery invariants failed: {combined_invariants}"
            )
        evidence["combined_disaster_rehearsal"] = {
            "postgres_restore": "PASS",
            "valkey_recovery": "PASS",
            "current_application": combined_app,
            "post_recovery_invariants": combined_invariants,
            "post_recovery_row_counts": combined_counts,
            "elapsed_application_and_worker_seconds": combined_seconds,
            "result": "PASS",
        }
        durable_before = restored_row_counts
        start_valkey(
            name=clean_valkey_name,
            port=clean_valkey_port,
            volume=None,
            tmpfs=True,
        )
        await wait_for_valkey(clean_valkey_port)
        clean_app_config = replace(
            restore_config,
            valkey_container=clean_valkey_name,
            valkey_port=clean_valkey_port,
        )
        loss_app = await migration.run_application_compatibility(
            clean_app_config,
            target_db,
            work_dir,
            label="total-valkey-loss",
        )
        loss_connection = await migration.connection_for(restore_config, target_db)
        try:
            durable_after = await migration.row_counts(restore_config, target_db)
            loss_invariants = await migration.invariant_counts(
                restore_config, target_db
            )
        finally:
            await loss_connection.close()
        durable_keys = (
            "logs",
            "pipeline_ledger",
            "pipeline_outbox",
            "pipeline_feature_inputs",
        )
        durable_preserved = all(
            durable_before.get(key) == durable_after.get(key) for key in durable_keys
        )
        if not durable_preserved or not migration.invariants_passed(loss_invariants):
            raise HarnessFailure(
                f"total Valkey loss changed durable state: before={durable_before}, "
                f"after={durable_after}, invariants={loss_invariants}"
            )
        evidence["total_valkey_loss"] = {
            "clean_valkey_started": True,
            "durable_db_state_before": {
                key: durable_before.get(key) for key in durable_keys
            },
            "durable_db_state_after": {
                key: durable_after.get(key) for key in durable_keys
            },
            "durable_state_preserved": durable_preserved,
            "current_application_and_worker": loss_app,
            "post_recovery_invariants": loss_invariants,
            "reconciliation_model": {
                "postgresql": "authoritative durable operational records",
                "valkey": "transport, consumer groups, pending delivery, cache, and heartbeat state",
                "clean_valkey_behavior": "worker recreates its stream group; committed PostgreSQL records remain",
            },
            "result": "PASS",
        }
        evidence["pitr_rehearsal"] = await pitr_rehearsal(
            config,
            work_dir=work_dir,
            run_id=run_id,
        )
        evidence["measured_recovery_timeline"] = {
            "remote_point_location_and_integrity_seconds": remote_inventory_seconds,
            "remote_download_and_postgresql_restore_seconds": restore_seconds,
            "pitr_recovery_seconds": evidence["pitr_rehearsal"]["recovery_seconds"],
            "valkey_recovery_seconds": valkey_result["restart_recovery_seconds"],
            "application_and_worker_start_seconds": combined_seconds,
            "measured_recovery_rehearsal_time_seconds": round(
                restore_seconds
                + remote_inventory_seconds
                + evidence["pitr_rehearsal"]["recovery_seconds"]
                + valkey_result["restart_recovery_seconds"]
                + combined_seconds,
                3,
            ),
            "approved_rto": "NOT DEFINED",
        }
        evidence["technical_recovery_granularity"] = {
            "logical_backup": "two manually invoked disposable remote points; production schedule not verified",
            "wal_archival": "PITR disposable rehearsal used 1-second archive timeout; production WAL state is outside this disposable harness and is recorded by Remediation 06C.1",
            "technical_potential_data_loss_window": "at least the interval since the latest successful logical backup when production WAL archival is inactive; the production interval is recorded separately by Remediation 06C.1",
            "approved_rpo": "NOT DEFINED",
        }
        return evidence
    finally:
        remove_container(clean_valkey_name)
        remove_volume(recovery_volume)
        remove_container(recovery_valkey_name)
        if restore_started:
            remove_container(restore_config.postgres_container)
        if source_started:
            remove_container(config.postgres_container)
        remove_container(minio_name)


def write_evidence(evidence: dict[str, Any]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"dr-{evidence['run_id']}.json"
    path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def main() -> int:
    run_id = secrets.token_hex(6)
    config = migration.make_config()
    config.postgres_container = f"logsentinel-06c-dr-source-{run_id}"
    config.valkey_container = f"logsentinel-06c-dr-unused-valkey-{run_id}"
    config.control_db = f"dr_source_{run_id}"
    with tempfile.TemporaryDirectory(prefix=f"logsentinel-06c-dr-{run_id}-") as raw:
        work_dir = Path(raw)
        evidence: dict[str, Any] = {
            "run_id": run_id,
            "status": "FAIL",
            "source_identity": migration.source_identity(),
        }
        try:
            evidence = asyncio.run(run_scenario(config, work_dir))
            path = write_evidence(evidence)
            print(
                json.dumps(
                    {"status": evidence["status"], "evidence": str(path)},
                    sort_keys=True,
                )
            )
            return 0
        except Exception as exc:
            evidence["status"] = "FAIL"
            evidence["failure_type"] = type(exc).__name__
            evidence["failure"] = safe_text(str(exc), config.postgres_password)
            path = write_evidence(evidence)
            print(json.dumps({"status": "FAIL", "evidence": str(path)}, sort_keys=True))
            return 1


if __name__ == "__main__":
    raise SystemExit(main())

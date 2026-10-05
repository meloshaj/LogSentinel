"""Background worker for Hot/Cold Storage Architecture."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ..core.database import get_engine
from ..core.settings import get_archive_settings
from ..security.redaction import sanitize_error_text
from .manifest import generate_sidecar_manifest
from .rehydration import cleanup_expired_rehydration_sessions
from .s3_client import get_s3_client, run_storage_io, read_object
from .serializer import async_serialize_to_parquet
from .verifier import ArchiveVerifier

logger = logging.getLogger("logsentinel.archive.worker")


class ArchiveWorker:
    def __init__(self, check_interval_seconds: float = 60.0):
        self.check_interval_seconds = check_interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None
        self.settings = get_archive_settings()
        self.s3_client = get_s3_client()
        self.verifier = ArchiveVerifier(self.s3_client)
        self.instance_id = str(uuid.uuid4())

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="archive_worker")
        logger.info("ArchiveWorker started (instance_id: %s)", self.instance_id)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ArchiveWorker stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._step_create_manifests()
                await self._step_process_state_machine()
                await cleanup_expired_rehydration_sessions()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "ArchiveWorker loop error: exception_type=%s detail=%s",
                    type(e).__name__,
                    sanitize_error_text(e),
                )

            await asyncio.sleep(self.check_interval_seconds)

    async def _step_create_manifests(self) -> None:
        """Find hot chunks older than retention period and create manifest records in HOT state."""
        engine = get_engine()
        retention_threshold = datetime.now(timezone.utc) - timedelta(
            days=self.settings.archive_hot_retention_days
        )

        query = text("""
            SELECT DISTINCT
                l.tenant_id,
                l.owner_user_id,
                c.chunk_schema,
                c.chunk_name,
                c.range_start,
                c.range_end
            FROM timescaledb_information.chunks AS c
            JOIN logs AS l
              ON l.ingested_at >= c.range_start
             AND l.ingested_at < c.range_end
            WHERE c.hypertable_name = 'logs'
              AND c.range_end < :retention_threshold
        """)

        async with engine.connect() as conn:
            result = await conn.execute(
                query, {"retention_threshold": retention_threshold}
            )
            chunks = result.mappings().all()

            for chunk in chunks:
                tenant_id = str(chunk["tenant_id"])
                owner_user_id = int(chunk["owner_user_id"])
                chunk_name = f"{chunk['chunk_schema']}.{chunk['chunk_name']}"
                idempotency_key = (
                    f"raw_logs:{tenant_id}:{owner_user_id}:{chunk_name}:"
                    f"{chunk['range_start'].isoformat()}:{chunk['range_end'].isoformat()}"
                )
                archive_id = uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)
                tenant_prefix = uuid.uuid5(
                    uuid.NAMESPACE_URL, f"{tenant_id}:{owner_user_id}"
                ).hex[:16]
                await conn.execute(
                    text("""
                        INSERT INTO archive_manifest (
                            archive_id, tenant_id, owner_user_id, dataset, range_start, range_end,
                            source_chunk_ids, schema_version, archive_format_version,
                            idempotency_key, object_key, sidecar_key, status, manifest_status
                        ) VALUES (
                            :archive_id, :tenant_id, :owner_user_id, 'raw_logs', :range_start, :range_end,
                            :source_chunk_ids, 1, 1, :idempotency_key,
                            :object_key, :sidecar_key, 'HOT', 'manifest_created'
                        )
                        ON CONFLICT (idempotency_key) DO NOTHING
                    """),
                    {
                        "archive_id": archive_id,
                        "tenant_id": tenant_id,
                        "owner_user_id": owner_user_id,
                        "range_start": chunk["range_start"],
                        "range_end": chunk["range_end"],
                        "source_chunk_ids": [chunk_name],
                        "idempotency_key": idempotency_key,
                        "object_key": f"raw_logs/{tenant_prefix}/{archive_id}.parquet",
                        "sidecar_key": f"raw_logs/{tenant_prefix}/{archive_id}.json",
                    },
                )
            await conn.commit()

    async def _step_process_state_machine(self) -> None:
        """Process one job from the state machine."""
        engine = get_engine()

        # 1. Lease a job
        lease_expires = datetime.now(timezone.utc) + timedelta(minutes=15)

        lease_query = text("""
            UPDATE archive_manifest
            SET lease_owner = :owner, lease_expires_at = :expires
            WHERE archive_id = (
                SELECT archive_id 
                FROM archive_manifest 
                WHERE status IN ('HOT', 'EXPORTING', 'STORED', 'VERIFIED')
                  AND (lease_owner IS NULL OR lease_expires_at < NOW())
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *;
        """)

        async with engine.connect() as conn:
            result = await conn.execute(
                lease_query, {"owner": self.instance_id, "expires": lease_expires}
            )
            row = result.mappings().first()
            if not row:
                return  # No work

            await conn.commit()

        record = dict(row)
        status = record["status"]
        archive_id = record["archive_id"]

        try:
            if status == "HOT":
                await self._transition_hot_to_exporting(record)
            elif status == "EXPORTING":
                await self._transition_exporting_to_stored(record)
            elif status == "STORED":
                await self._transition_stored_to_verified(record)
            elif status == "VERIFIED":
                await self._transition_verified_to_hot_deleted(record)

        except Exception as e:
            logger.error(
                "Error processing archive %s (status %s): exception_type=%s detail=%s",
                archive_id,
                status,
                type(e).__name__,
                sanitize_error_text(e),
            )
            # Release lease on error
            async with engine.connect() as conn:
                await conn.execute(
                    text(
                        """
                        UPDATE archive_manifest
                        SET lease_owner = NULL,
                            attempt_count = COALESCE(attempt_count, 0) + 1,
                            failure_reason = :reason
                        WHERE archive_id = :id
                        """
                    ),
                    {"id": archive_id, "reason": type(e).__name__},
                )
                await conn.commit()

    async def _transition_hot_to_exporting(self, record: dict) -> None:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "UPDATE archive_manifest SET status = 'EXPORTING', manifest_status = 'uploading' WHERE archive_id = :id"
                ),
                {"id": record["archive_id"]},
            )
            await conn.commit()

    async def _transition_exporting_to_stored(self, record: dict) -> None:
        engine = get_engine()

        # Read from logs table
        query = text("""
            SELECT * FROM logs
            WHERE tenant_id = :tenant_id 
              AND owner_user_id = :owner_user_id
              AND ingested_at >= :start 
              AND ingested_at < :end
            ORDER BY ingested_at, id
            LIMIT :row_limit
        """)

        async with engine.connect() as conn:
            result = await conn.execute(
                query,
                {
                    "tenant_id": record["tenant_id"],
                    "owner_user_id": record["owner_user_id"],
                    "start": record["range_start"],
                    "end": record["range_end"],
                    "row_limit": self.settings.max_export_rows + 1,
                },
            )
            rows = [dict(r) for r in result.mappings().all()]

        if len(rows) > self.settings.max_export_rows:
            raise ValueError("archive export row limit exceeded")

        parquet_bytes, stats = await async_serialize_to_parquet(rows)
        if len(parquet_bytes) > self.settings.max_archive_bytes:
            raise ValueError("archive export byte limit exceeded")

        # Write to S3
        created = await run_storage_io(
            self.s3_client.put_if_absent,
            record["object_key"],
            parquet_bytes,
            "application/vnd.apache.parquet",
        )
        if not created:
            existing = await run_storage_io(
                read_object,
                self.s3_client,
                record["object_key"],
                self.settings.max_archive_bytes,
            )
            if existing != parquet_bytes:
                raise ValueError("immutable archive object conflict")

        # Update record with stats to generate correct sidecar
        record.update(stats)
        sidecar_bytes = generate_sidecar_manifest(record)
        await run_storage_io(
            self.s3_client.put_if_absent,
            record["sidecar_key"],
            sidecar_bytes,
            "application/json",
        )

        retention_deadlines: list[datetime] = []
        for row in rows:
            deadline = row.get("retention_deadline_at")
            if isinstance(deadline, datetime):
                retention_deadlines.append(deadline)

        async with engine.connect() as conn:
            await conn.execute(
                text("""
                    UPDATE archive_manifest 
                    SET status = 'STORED',
                        manifest_status = 'uploaded',
                        row_count = :row_count, 
                        min_ingested_at = :min_ingested_at,
                        max_ingested_at = :max_ingested_at,
                        sha256 = :sha256,
                        compressed_bytes = :compressed_bytes,
                        object_size = :object_size,
                        retention_deadline_at = :retention_deadline_at,
                        failure_reason = NULL
                    WHERE archive_id = :id
                """),
                {
                    "id": record["archive_id"],
                    "row_count": stats["row_count"],
                    "min_ingested_at": stats["min_ingested_at"],
                    "max_ingested_at": stats["max_ingested_at"],
                    "sha256": stats["sha256"],
                    "compressed_bytes": stats["compressed_bytes"],
                    "object_size": stats["compressed_bytes"],
                    "retention_deadline_at": max(retention_deadlines, default=None),
                },
            )
            await conn.commit()

    async def _transition_stored_to_verified(self, record: dict) -> None:
        is_valid = await self.verifier.async_verify_archive(record)

        engine = get_engine()
        async with engine.connect() as conn:
            if is_valid:
                await conn.execute(
                    text(
                        "UPDATE archive_manifest SET status = 'VERIFIED', manifest_status = 'checksummed', verified_at = NOW() WHERE archive_id = :id"
                    ),
                    {"id": record["archive_id"]},
                )
            else:
                await conn.execute(
                    text(
                        "UPDATE archive_manifest SET status = 'CORRUPT', manifest_status = 'verification_failed', failure_reason = 'checksum_or_schema_mismatch' WHERE archive_id = :id"
                    ),
                    {"id": record["archive_id"]},
                )
            await conn.commit()

    async def _transition_verified_to_hot_deleted(self, record: dict) -> None:
        engine = get_engine()
        async with engine.connect() as conn:
            pending = await conn.execute(
                text("""
                    SELECT 1
                    FROM archive_manifest
                    WHERE source_chunk_ids && :chunk_ids
                      AND archive_id <> :id
                      AND status NOT IN ('VERIFIED', 'HOT_DELETED')
                    LIMIT 1
                """),
                {"chunk_ids": record["source_chunk_ids"], "id": record["archive_id"]},
            )
            if pending.first() is not None:
                await conn.execute(
                    text(
                        "UPDATE archive_manifest SET lease_owner = NULL WHERE archive_id = :id"
                    ),
                    {"id": record["archive_id"]},
                )
                await conn.commit()
                return

            await conn.execute(
                text(
                    "UPDATE archive_manifest SET manifest_status = 'deletable' WHERE archive_id = :id"
                ),
                {"id": record["archive_id"]},
            )

            # Safely drop chunks using TimescaleDB API
            for chunk in record["source_chunk_ids"]:
                # The actual drop_chunks function handles safe deletion
                await conn.execute(
                    text("SELECT drop_chunks(:chunk_name::regclass)"),
                    {"chunk_name": chunk},
                )

            await conn.execute(
                text(
                    "UPDATE archive_manifest SET status = 'HOT_DELETED', manifest_status = 'deleted', deleted_from_hot_at = NOW(), completed_at = NOW() WHERE archive_id = :id"
                ),
                {"id": record["archive_id"]},
            )
            await conn.commit()


async def standalone_main():
    """Standalone entrypoint for running the archive worker out-of-process."""
    from ..core.database import dispose_engine, init_engine
    from ..core.settings import get_database_settings

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    # Initialize database engine
    db_settings = get_database_settings()
    init_engine(db_settings)

    worker = ArchiveWorker()
    worker.start()

    try:
        # Keep the event loop running
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
    finally:
        await worker.stop()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(standalone_main())

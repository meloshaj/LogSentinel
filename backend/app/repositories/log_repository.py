"""Bulk persistence for parsed Drain3 logs."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import (
    and_,
    delete,
    insert,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine
from ..core.orm import LogRecord
from ..core.pipeline_identity import feature_contribution_key
from ..core.pipeline_orm import ledger
from ..models import ParsedLog
from ..observability.metrics import (
    DOWNSTREAM_REGISTRATION_FAILURES,
    RAW_ACCEPTED_TOTAL,
    RAW_REPLAY_TOTAL,
)
from ..schemas.alerting import IncidentAlertPayload
from ..security.data_scope import DataScope
from ..services.alerting import enqueue_incident_alert
from ..services.durable_queue import enqueue

logs_table = LogRecord.__table__
logger = logging.getLogger("logsentinel.log_repository")


class PersistStatus(str, Enum):
    """Outcome for one tenant-qualified logical source event."""

    NEWLY_INSERTED = "newly_inserted"
    ALREADY_PROCESSED = "already_processed"
    FAILED = "failed"


@dataclass(frozen=True)
class PersistResult:
    """Typed raw-acceptance result consumed by the stream worker."""

    tenant_id: str
    event_id: str
    status: PersistStatus


class PersistResults(list[PersistResult]):
    """Ordered per-event results with an integer-count compatibility view."""

    @property
    def newly_inserted_count(self) -> int:
        return sum(item.status is PersistStatus.NEWLY_INSERTED for item in self)

    @property
    def replay_count(self) -> int:
        return sum(item.status is PersistStatus.ALREADY_PROCESSED for item in self)

    @property
    def failed_count(self) -> int:
        return sum(item.status is PersistStatus.FAILED for item in self)

    def __eq__(self, other: object) -> bool:
        # Existing repository callers historically compared the return value
        # with the number of inserted rows. Keep that narrow compatibility
        # while exposing the full typed result to new callers.
        if isinstance(other, int):
            return self.newly_inserted_count == other
        return super().__eq__(other)


class LogRepository:
    """Repository for writing parsed logs to PostgreSQL."""

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        """Return the injected engine or fall back to the global pool."""
        if self._engine is not None:
            return self._engine
        return get_engine()

    def _partition_log_batch(
        self, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Partition logs into live (>= 2 days old) and late (< 2 days old) to avoid uncompressing chunks."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=2)
        live_logs = []
        late_logs = []
        for row in rows:
            log_time = row["created_at"]

            # Normalize to UTC aware datetime
            if isinstance(log_time, str):
                log_time = datetime.fromisoformat(log_time.replace("Z", "+00:00"))
            if log_time.tzinfo is None:
                log_time = log_time.replace(tzinfo=timezone.utc)
            else:
                log_time = log_time.astimezone(timezone.utc)

            if log_time >= cutoff:
                live_logs.append(row)
            else:
                late_logs.append(row)
        return live_logs, late_logs

    @staticmethod
    def _serialize_for_copy(row: dict[str, Any]) -> tuple:
        """Normalize a row dict into a tuple strictly typed for ``asyncpg.copy_records_to_table``.

        Each field is coerced to its binary COPY-compatible type:
        - ``parameters`` / ``metadata``: ``json.dumps()`` (str), with safe defaults.
        - ``source``, ``environment``: fallback to ``"unknown"``/``"production"`` if ``None``.
        - ``parsed_at``: defaults to ``datetime.now(UTC)`` if ``None``.
        - All other fields: passed through as-is (str / datetime).
        """
        parameters = row.get("parameters")
        if isinstance(parameters, (dict, list)):
            parameters = json.dumps(parameters)
        elif parameters is None:
            parameters = "[]"

        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            metadata = json.dumps(metadata)
        elif metadata is None:
            metadata = "{}"

        return (
            row.get("tenant_id", "default"),
            row["owner_user_id"],
            row["id"],
            row.get("event_id") or row["id"],
            row["timestamp"],
            row["service"],
            row["raw_message"],
            row["template_id"],
            row.get("template_text"),
            parameters,
            row.get("level"),
            row.get("source") or "unknown",
            row.get("environment") or "production",
            row.get("correlation_id"),
            metadata,
            row.get("parsed_at") or datetime.now(timezone.utc),
            row["created_at"],
            row.get("ingested_at") or datetime.now(timezone.utc),
        )

    async def bulk_insert_parsed_logs(
        self, parsed_logs: Sequence[ParsedLog]
    ) -> PersistResults:
        """Persist a batch and return an explicit result for every event.

        Database failures roll back the transaction and are represented as
        ``FAILED`` results. The batch manager treats those results as a failed
        sink attempt, so a stream message remains unacknowledged and can be
        retried.
        """
        parsed_logs = list(parsed_logs)
        if not parsed_logs:
            return PersistResults()
        if any(
            int(log.owner_user_id) <= 0 or log.tenant_id == "default"
            for log in parsed_logs
        ):
            raise ValueError("parsed logs require an authoritative tenant and owner")
        try:
            return await self._bulk_insert_parsed_logs(parsed_logs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Raw persistence failed stage=raw_log category=%s event_count=%d",
                type(exc).__name__,
                len(parsed_logs),
            )
            return PersistResults(
                PersistResult(
                    tenant_id=str(parsed_log.tenant_id),
                    event_id=str(parsed_log.event_id or parsed_log.id),
                    status=PersistStatus.FAILED,
                )
                for parsed_log in parsed_logs
            )

    async def _bulk_insert_parsed_logs(
        self, parsed_logs: Sequence[ParsedLog]
    ) -> PersistResults:
        """Persist raw logs and register downstream work in one transaction.

        The returned list has one item for every supplied logical event. A
        duplicate raw ledger claim is reported explicitly as
        ``ALREADY_PROCESSED``; it is not inferred from a batch row count.
        """
        if not parsed_logs:
            return PersistResults()

        parsed_logs = list(parsed_logs)
        rows = [self.map_parsed_log(parsed_log) for parsed_log in parsed_logs]
        event_keys = [
            (row["tenant_id"], row["owner_user_id"], row["event_id"]) for row in rows
        ]

        async with self.engine.connect() as connection:
            # The ordinary ledger is the idempotency authority. It is kept
            # outside the hypertable because PostgreSQL requires every unique
            # hypertable index to include the time partition column.
            ledger_result = await connection.execute(
                pg_insert(ledger)
                .values(
                    [
                        {
                            "tenant_id": row["tenant_id"],
                            "owner_user_id": row["owner_user_id"],
                            "stage": "raw_log",
                            "event_id": row["event_id"],
                        }
                        for row in rows
                    ]
                )
                .on_conflict_do_nothing()
                .returning(
                    ledger.c.tenant_id, ledger.c.owner_user_id, ledger.c.event_id
                )
            )
            accepted = {
                (item.tenant_id, item.owner_user_id, item.event_id)
                for item in ledger_result
            }
            # A malformed/retried delivery can contain the same logical event
            # more than once in one batch.  The ledger RETURNING set tells us
            # which key this transaction won, but it must not be expanded
            # back into every duplicate input row or COPY/INSERT would try to
            # write the raw record twice.
            new_rows: list[dict[str, Any]] = []
            inserted_keys: set[tuple[str, int, str]] = set()
            for row in rows:
                key = (row["tenant_id"], row["owner_user_id"], row["event_id"])
                if key in accepted and key not in inserted_keys:
                    new_rows.append(row)
                    inserted_keys.add(key)
            if new_rows:
                live_logs, late_logs = self._partition_log_batch(new_rows)
            else:
                live_logs, late_logs = [], []
            if live_logs:
                # Extract underlying asyncpg connection for maximum throughput COPY operation
                raw_conn = await connection.get_raw_connection()
                asyncpg_conn = raw_conn.driver_connection

                tuples = [self._serialize_for_copy(row) for row in live_logs]

                await asyncpg_conn.copy_records_to_table(  # type: ignore
                    "logs",
                    records=tuples,
                    columns=[
                        "tenant_id",
                        "owner_user_id",
                        "id",
                        "event_id",
                        "timestamp",
                        "service",
                        "raw_message",
                        "template_id",
                        "template_text",
                        "parameters",
                        "level",
                        "source",
                        "environment",
                        "correlation_id",
                        "metadata",
                        "parsed_at",
                        "created_at",
                        "ingested_at",
                    ],
                )

            if late_logs:
                logging.warning(
                    f"Intercepted {len(late_logs)} late-arriving logs (>2 days old). "
                    "Routing via isolated INSERT path to prevent chunk decompression lock."
                )
                SUB_BATCH_SIZE = 500
                for i in range(0, len(late_logs), SUB_BATCH_SIZE):
                    sub_batch = late_logs[i : i + SUB_BATCH_SIZE]
                    stmt = insert(logs_table)  # type: ignore
                    await connection.execute(stmt, sub_batch)

            # A raw ledger claim is not sufficient for correctness. Every
            # supplied event (including a replay missing a legacy downstream
            # record) is reconciled into the deterministic feature stage. The
            # outbox uniqueness key makes this idempotent and the transaction
            # couples registration to raw acceptance.
            try:
                for parsed_log, row in zip(parsed_logs, rows):
                    payload = parsed_log.model_dump(mode="json")
                    payload["tenant_id"] = row["tenant_id"]
                    payload["event_id"] = row["event_id"]
                    await enqueue(
                        connection,
                        tenant_id=row["tenant_id"],
                        owner_user_id=row["owner_user_id"],
                        topic="feature_contribution",
                        dedup_key=feature_contribution_key(
                            row["tenant_id"],
                            row["event_id"],
                            owner_user_id=row["owner_user_id"],
                        ),
                        payload=payload,
                        event_id=row["event_id"],
                    )

                    # Error-level ingestion is a correctness-relevant alert
                    # path. Register it in this same transaction so raw commit
                    # cannot be separated from its durable alert acceptance.
                    if (
                        str(row.get("level", "")).lower() == "error"
                        and str(row["tenant_id"]).strip() != "default"
                    ):
                        await enqueue_incident_alert(
                            connection,
                            IncidentAlertPayload(
                                tenant_id=row["tenant_id"],
                                owner_user_id=row["owner_user_id"],
                                incident_id=row["event_id"],
                                root_cause_service=row["service"],
                                triggering_template=(
                                    row.get("template_text") or row["raw_message"]
                                ),
                                affected_services=[],
                                propagation_chain=[row["service"]],
                                confidence_score=0.5,
                                is_critical=False,
                            ),
                        )
            except Exception:
                DOWNSTREAM_REGISTRATION_FAILURES.inc()
                raise

            await connection.commit()

        reported_new: set[tuple[str, int, str]] = set()
        results = PersistResults()
        for tenant_id, owner_user_id, event_id in event_keys:
            key = (tenant_id, owner_user_id, event_id)
            status = PersistStatus.ALREADY_PROCESSED
            if key in accepted and key not in reported_new:
                status = PersistStatus.NEWLY_INSERTED
                reported_new.add(key)
            results.append(PersistResult(tenant_id, event_id, status))
        RAW_ACCEPTED_TOTAL.inc(results.newly_inserted_count)
        RAW_REPLAY_TOTAL.inc(results.replay_count)
        return results

    async def get_recent_correlation_evidence(
        self,
        *,
        tenant_id: str | None = None,
        owner_user_id: int | None = None,
        start_time: datetime,
        end_time: datetime,
        services: Sequence[str] | None = None,
        correlation_ids: Sequence[str] | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Return bounded recent log rows needed for service/trace evidence."""
        conditions = [
            logs_table.c.timestamp >= start_time,
            logs_table.c.timestamp <= end_time,
            # Mandatory chunk-exclusion filter for TimescaleDB
            logs_table.c.ingested_at >= start_time,
            logs_table.c.ingested_at <= end_time,
        ]
        if tenant_id is not None:
            conditions.append(logs_table.c.tenant_id == tenant_id)
        if owner_user_id is not None:
            conditions.append(logs_table.c.owner_user_id == owner_user_id)
        cleaned_services = sorted({service for service in services or [] if service})
        cleaned_correlation_ids = sorted(
            {
                correlation_id
                for correlation_id in correlation_ids or []
                if correlation_id
            }
        )
        if cleaned_correlation_ids:
            conditions.append(logs_table.c.correlation_id.in_(cleaned_correlation_ids))
            if cleaned_services:
                conditions.append(logs_table.c.service.in_(cleaned_services))
        elif cleaned_services:
            conditions.append(logs_table.c.service.in_(cleaned_services))

        stmt = (
            select(
                logs_table.c.id,
                logs_table.c.timestamp,
                logs_table.c.service,
                logs_table.c.level,
                logs_table.c.correlation_id,
                logs_table.c.metadata,
            )
            .where(and_(*conditions))
            .order_by(logs_table.c.timestamp.desc(), logs_table.c.service.asc())
            .limit(max(0, limit))
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]

    async def get_log_by_id(
        self,
        scope: DataScope,
        log_id: str,
        ingested_at_start: datetime,
        ingested_at_end: datetime,
    ) -> dict[str, Any] | None:
        """Fetch a single log by ID with mandatory time bounds for chunk exclusion."""
        stmt = select(logs_table).where(
            and_(
                logs_table.c.tenant_id == scope.tenant_id,
                logs_table.c.owner_user_id == scope.owner_user_id,
                logs_table.c.id == log_id,
                logs_table.c.ingested_at >= ingested_at_start,
                logs_table.c.ingested_at <= ingested_at_end,
            )
        )
        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
            return dict(row) if row else None

    async def delete_log(
        self,
        scope: DataScope,
        log_id: str,
        ingested_at_start: datetime,
        ingested_at_end: datetime,
    ) -> bool:
        """Delete a single log by ID with mandatory time bounds for chunk exclusion."""
        stmt = delete(logs_table).where(  # type: ignore
            and_(
                logs_table.c.tenant_id == scope.tenant_id,
                logs_table.c.owner_user_id == scope.owner_user_id,
                logs_table.c.id == log_id,
                logs_table.c.ingested_at >= ingested_at_start,
                logs_table.c.ingested_at <= ingested_at_end,
            )
        )
        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            return result.rowcount > 0

    async def get_recent_logs(
        self, scope: DataScope | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Return the most recent logs for backfilling the UI."""
        stmt = (
            select(
                logs_table.c.id,
                logs_table.c.timestamp,
                logs_table.c.service,
                logs_table.c.raw_message,
                logs_table.c.level,
                logs_table.c.template_id,
                logs_table.c.template_text,
                logs_table.c.metadata,
            )
            .order_by(logs_table.c.ingested_at.desc())
            .limit(limit)
        )
        if scope is not None:
            stmt = stmt.where(
                logs_table.c.tenant_id == scope.tenant_id,
                logs_table.c.owner_user_id == scope.owner_user_id,
            )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]

    async def get_logs_paginated(
        self,
        scope: DataScope,
        page: int = 1,
        limit: int = 50,
        service: str | None = None,
        level: str | None = None,
    ) -> dict[str, Any]:
        """Compatibility wrapper that returns the first keyset page.

        Large OFFSET scans and automatic full-table counts are intentionally
        gone.  Callers must use ``next_cursor`` for subsequent pages.
        """
        if page != 1:
            raise ValueError("offset pagination is retired; use the cursor parameter")
        result = await self.get_logs_cursor(
            scope=scope,
            limit=limit,
            service=service,
            level=level,
        )
        result.update({"page": 1, "pages": None, "total": None})
        return result

    async def get_logs_cursor(
        self,
        scope: DataScope,
        limit: int = 50,
        cursor: str | None = None,
        service: str | None = None,
        level: str | None = None,
    ) -> dict[str, Any]:
        """Fetch logs with bounded keyset pagination instead of OFFSET scans."""
        conditions = [
            logs_table.c.tenant_id == scope.tenant_id,
            logs_table.c.owner_user_id == scope.owner_user_id,
        ]
        if service:
            conditions.append(logs_table.c.service == service)
        if level:
            conditions.append(logs_table.c.level == level)
        if cursor:
            try:
                raw = base64.urlsafe_b64decode(cursor.encode("ascii") + b"===")
                marker = json.loads(raw.decode("utf-8"))
                marker_time = datetime.fromisoformat(marker["ingested_at"])
                marker_id = str(marker["id"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid log cursor") from exc
            conditions.append(
                or_(
                    logs_table.c.ingested_at < marker_time,
                    and_(
                        logs_table.c.ingested_at == marker_time,
                        logs_table.c.id < marker_id,
                    ),
                )
            )

        stmt = (
            select(
                logs_table.c.id,
                logs_table.c.timestamp,
                logs_table.c.ingested_at,
                logs_table.c.service,
                logs_table.c.raw_message,
                logs_table.c.level,
                logs_table.c.template_id,
                logs_table.c.template_text,
                logs_table.c.metadata,
            )
            .where(and_(*conditions))
            .order_by(logs_table.c.ingested_at.desc(), logs_table.c.id.desc())
            .limit(limit + 1)
        )
        async with self.engine.connect() as conn:
            rows = [dict(row) for row in (await conn.execute(stmt)).mappings().all()]

        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "ingested_at": last["ingested_at"].isoformat(),
                            "id": last["id"],
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                .decode("ascii")
                .rstrip("=")
            )
        for row in rows:
            row.pop("ingested_at", None)
        return {"items": rows, "limit": limit, "next_cursor": next_cursor}

    @staticmethod
    def map_parsed_log(parsed_log: ParsedLog) -> dict[str, Any]:
        """Convert a validated ParsedLog into one database insert row."""
        return {
            "id": parsed_log.id,
            "event_id": getattr(parsed_log, "event_id", None) or parsed_log.id,
            "tenant_id": getattr(parsed_log, "tenant_id", "default"),
            "owner_user_id": int(parsed_log.owner_user_id),
            "timestamp": parsed_log.timestamp,
            "service": getattr(parsed_log, "service_name", parsed_log.service),
            "raw_message": getattr(
                parsed_log,
                "message",
                getattr(parsed_log, "raw", parsed_log.raw_message),
            ),
            "template_id": parsed_log.template_id
            if parsed_log.template_id
            else "UNPARSED_0000",
            "template_text": parsed_log.template_text,
            "parameters": [dict(item) for item in parsed_log.parameters],
            "level": parsed_log.level,
            "source": parsed_log.source,
            "environment": parsed_log.environment,
            "correlation_id": getattr(parsed_log, "correlation_id", None),
            "metadata": _json_safe_dict(parsed_log.metadata),
            "parsed_at": parsed_log.parsed_at,
            "created_at": parsed_log.timestamp
            if getattr(parsed_log, "timestamp", None)
            else datetime.now(timezone.utc),
            "ingested_at": datetime.now(timezone.utc),
        }


def _json_safe_dict(values: dict[str, Any]) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            serialized[key] = (
                value.astimezone(timezone.utc)
                .isoformat()
                .replace(
                    "+00:00",
                    "Z",
                )
            )
        elif isinstance(value, dict):
            serialized[key] = _json_safe_dict(value)
        elif isinstance(value, list):
            serialized[key] = [
                item.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if isinstance(item, datetime)
                else item
                for item in value
            ]
        else:
            serialized[key] = value
    return serialized

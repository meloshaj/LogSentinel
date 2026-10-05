"""Standalone worker for periodic retraining of the anomaly detection model."""

import asyncio
import hashlib
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

from ..core.settings import get_database_settings
from ..security.redaction import sanitize_error_text
from ..ml.anomaly_detector import IsolationForestAnomalyDetector
from ..ml.model_registry import promote_detector
from ..models import FeatureVector

# Add backend directory to sys.path if running as a standalone script
_worker_dir = Path(__file__).resolve().parent
_backend_dir = _worker_dir.parents[1]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("logsentinel.retrain_worker")


def compute_checksum(filepath: Path) -> str:
    """Compute SHA256 checksum of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


async def retrain_model() -> None:
    """Query recent feature vectors and retrain the anomaly detection model."""
    logger.info("Starting periodic retraining of IsolationForest model")

    db_settings = get_database_settings()

    try:
        conn = await asyncpg.connect(**db_settings.asyncpg_connect_kwargs())
    except Exception as e:
        logger.error(
            "Failed to connect to database: exception_type=%s detail=%s",
            type(e).__name__,
            sanitize_error_text(e),
        )
        sys.exit(1)

    try:
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

        logger.info("Querying feature_windows tenants since %s", seven_days_ago)
        tenant_rows = await conn.fetch(
            """
            SELECT DISTINCT tenant_id, owner_user_id
            FROM feature_windows
            WHERE created_at >= $1
            ORDER BY tenant_id, owner_user_id
            """,
            seven_days_ago,
        )

        if not tenant_rows:
            logger.warning(
                "No feature vectors found in the last 7 days. Aborting retraining."
            )
            return

        feature_vectors_by_scope: dict[tuple[str, int], list[FeatureVector]] = (
            defaultdict(list)
        )
        total_rows = 0
        for tenant_row in tenant_rows:
            tenant_id = str(tenant_row["tenant_id"])
            owner_user_id = int(tenant_row["owner_user_id"])
            rows = await conn.fetch(
                """
                SELECT tenant_id, owner_user_id, window_id, start_time, end_time, service,
                       log_count, feature_vector, created_at
                FROM feature_windows
                WHERE tenant_id = $1 AND owner_user_id = $2 AND created_at >= $3
                ORDER BY created_at
                """,
                tenant_id,
                owner_user_id,
                seven_days_ago,
            )
            total_rows += len(rows)
            for row in rows:
                try:
                    features = (
                        json.loads(row["feature_vector"])
                        if isinstance(row["feature_vector"], str)
                        else row["feature_vector"]
                    )
                    fv = FeatureVector(
                        window_id=row["window_id"],
                        timestamp=row["created_at"],
                        window_start=row["start_time"],
                        window_end=row["end_time"],
                        tenant_id=tenant_id,
                        owner_user_id=owner_user_id,
                        log_count=row["log_count"],
                        unique_templates=features.get("unique_templates", 0),
                        error_count=features.get("error_count", 0),
                        warning_count=features.get("warning_count", 0),
                        template_frequencies=features.get("template_frequencies", {}),
                        template_entropy=features.get("template_entropy"),
                        service_distribution=features.get("service_distribution", {}),
                        logs_per_second=features.get("logs_per_second"),
                        features=features,
                    )
                    feature_vectors_by_scope[(tenant_id, owner_user_id)].append(fv)
                except Exception:
                    logger.warning("Failed to parse row %s", row["window_id"])

        logger.info("Fetched %d feature window records", total_rows)

        if not feature_vectors_by_scope:
            logger.warning("No valid feature vectors parsed. Aborting retraining.")
            return

        logger.info(
            "Training tenant-specific IsolationForest models for %d tenants",
            len(feature_vectors_by_scope),
        )
        models_dir = _backend_dir / "models"
        for (
            tenant_id,
            owner_user_id,
        ), feature_vectors in feature_vectors_by_scope.items():
            detector = IsolationForestAnomalyDetector()
            detector.train(feature_vectors)
            tenant_hash = hashlib.sha256(
                f"{tenant_id}:{owner_user_id}".encode()
            ).hexdigest()[:32]
            range_start = min(
                vector.window_start or vector.timestamp for vector in feature_vectors
            )
            range_end = max(
                vector.window_end or vector.timestamp for vector in feature_vectors
            )
            metadata = promote_detector(
                base_path=models_dir / "isolation_forest.joblib",
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                detector=detector,
                training_range=f"[{range_start.isoformat()},{range_end.isoformat()})",
            )
            checksum = metadata["checksum"]
            logger.info(
                "Promoting tenant model hash=%s samples=%d checksum=%s",
                tenant_hash,
                len(feature_vectors),
                checksum,
            )
            # Database metadata gives operators and rollback tooling an
            # authoritative tenant/version/checksum record.  The advisory
            # lock serializes promotion across retraining processes.
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))", tenant_id
                )
                await conn.execute(
                    "UPDATE model_artifacts SET status = 'previous' "
                    "WHERE tenant_id = $1 AND owner_user_id = $2 AND model_id = $3 AND status = 'active'",
                    tenant_id,
                    owner_user_id,
                    metadata["model_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO model_artifacts
                        (tenant_id, owner_user_id, model_id, version, training_range,
                         feature_schema_version, artifact_uri, checksum,
                         training_metadata, status, promoted_at)
                    VALUES ($1, $2, $3, $4, $5::tstzrange, $6, $7, $8, $9::jsonb, 'active', NOW())
                    ON CONFLICT (tenant_id, owner_user_id, model_id, version)
                    DO UPDATE SET checksum = EXCLUDED.checksum,
                                  status = 'active', promoted_at = NOW()
                    """,
                    tenant_id,
                    owner_user_id,
                    metadata["model_id"],
                    metadata["version"],
                    metadata["training_range"],
                    metadata["feature_schema_version"],
                    metadata["artifact_uri"],
                    checksum,
                    json.dumps(
                        {
                            "sample_count": len(feature_vectors),
                            "training_range": metadata["training_range"],
                        }
                    ),
                )

        logger.info("Retraining completed successfully.")

    except Exception as e:
        logger.error(
            "Error during retraining: exception_type=%s detail=%s",
            type(e).__name__,
            sanitize_error_text(e),
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(retrain_model())

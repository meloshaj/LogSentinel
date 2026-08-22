"""Standalone worker for periodic retraining of the anomaly detection model."""

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

# Add backend directory to sys.path if running as a standalone script
_worker_dir = Path(__file__).resolve().parent
_backend_dir = _worker_dir.parents[1]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from app.core.settings import get_database_settings
from app.ml.anomaly_detector import IsolationForestAnomalyDetector
from app.models import FeatureVector

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
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
        logger.error("Failed to connect to database: %s", e)
        sys.exit(1)
        
    try:
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        
        logger.info("Querying feature_windows since %s", seven_days_ago)
        query = """
            SELECT window_id, start_time, end_time, service, log_count, feature_vector, created_at
            FROM feature_windows
            WHERE created_at >= $1
        """
        rows = await conn.fetch(query, seven_days_ago)
        logger.info("Fetched %d feature window records", len(rows))
        
        if not rows:
            logger.warning("No feature vectors found in the last 7 days. Aborting retraining.")
            return
            
        feature_vectors = []
        for row in rows:
            try:
                features = json.loads(row["feature_vector"]) if isinstance(row["feature_vector"], str) else row["feature_vector"]
                fv = FeatureVector(
                    window_id=row["window_id"],
                    timestamp=row["created_at"],
                    window_start=row["start_time"],
                    window_end=row["end_time"],
                    log_count=row["log_count"],
                    unique_templates=features.get("unique_templates", 0),
                    error_count=features.get("error_count", 0),
                    warning_count=features.get("warning_count", 0),
                    template_frequencies=features.get("template_frequencies", {}),
                    template_entropy=features.get("template_entropy"),
                    service_distribution=features.get("service_distribution", {}),
                    logs_per_second=features.get("logs_per_second"),
                    features=features
                )
                feature_vectors.append(fv)
            except Exception as e:
                logger.warning("Failed to parse row %s: %s", row["window_id"], e)
                
        if not feature_vectors:
            logger.warning("No valid feature vectors parsed. Aborting retraining.")
            return
            
        logger.info("Training IsolationForest model on %d feature vectors", len(feature_vectors))
        detector = IsolationForestAnomalyDetector()
        detector.train(feature_vectors)
        
        models_dir = _backend_dir / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        
        final_model_path = models_dir / "isolation_forest.joblib"
        tmp_model_path = models_dir / (
            "isolation_forest_tmp_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.joblib"
        )
        
        logger.info("Exporting trained model to temporary file %s", tmp_model_path)
        detector.save_model(tmp_model_path)
        
        checksum = compute_checksum(tmp_model_path)
        logger.info("Model artifact checksum (SHA256): %s", checksum)
        
        logger.info("Performing atomic file swap to %s", final_model_path)
        os.replace(tmp_model_path, final_model_path)
        
        logger.info("Retraining completed successfully.")
        
    except Exception as e:
        logger.exception("Error during retraining: %s", e)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(retrain_model())

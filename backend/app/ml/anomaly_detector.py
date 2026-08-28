"""Isolation Forest-based anomaly detection for feature vectors.

This module consumes feature vectors produced by the sliding window extractor
and produces structured anomaly predictions without coupling the detection
logic to the feature extraction pipeline.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import joblib
from sklearn.ensemble import IsolationForest

from ..models import FeatureVector
from .anomaly_scoring import normalize_isolation_forest_score

logger = logging.getLogger("logsentinel.anomaly_detector")

FEATURE_COLUMNS = [
    "log_count",
    "info_count",
    "warning_count",
    "error_count",
    "error_ratio",
    "active_services",
    "unique_templates",
    "dominant_service_count",
    "dominant_template_count",
    "logs_per_second",
    "avg_logs_per_minute",
    "burst_indicator",
]

CANONICAL_MODEL_FILENAME = "isolation_forest.joblib"


def get_canonical_model_path() -> Path:
    """Return the one model artifact path used by training and runtime."""
    return Path(__file__).resolve().parents[2] / "models" / CANONICAL_MODEL_FILENAME


class IsolationForestAnomalyDetector:
    """
    Train and use an IsolationForest model over feature vectors.

    This class wraps the scikit-learn IsolationForest algorithm, adapting it
    specifically for LogSentinel feature vectors. It manages training,
    serialization, and batch prediction with normalized anomaly scores.
    """

    def __init__(self, random_state: int = 42, contamination: float = 0.1) -> None:
        """
        Initialize the anomaly detector.

        Args:
            random_state: Seed for reproducible IsolationForest results.
            contamination: The proportion of outliers in the data set.
        """
        self.random_state: int = random_state
        self.contamination: float = contamination
        self.model: IsolationForest | None = None
        self.model_version: str = "isolation_forest_v1"
        self.training_samples: int = 0
        self.model_path: Path | None = None
        self.inference_total: int = 0
        self.inference_errors_total: int = 0
        self.anomalies_total: int = 0

    def train(self, feature_vectors: list[FeatureVector]) -> IsolationForest:
        """
        Train the IsolationForest model on a collection of feature vectors.

        Args:
            feature_vectors: A list of FeatureVector instances to train on.

        Returns:
            IsolationForest: The trained scikit-learn model instance.

        Raises:
            ValueError: If the feature_vectors list is empty.
        """
        if not feature_vectors:
            raise ValueError("At least one feature vector is required for training")

        matrix: list[list[float]] = self._to_feature_matrix(feature_vectors)
        self.model = IsolationForest(
            n_estimators=100,
            contamination=self.contamination,
            random_state=self.random_state,
            n_jobs=-1,
        )
        self.model.fit(matrix)
        self.training_samples = len(matrix)
        return self.model

    def predict(self, feature_vector: FeatureVector) -> dict[str, Any]:
        """Return a structured anomaly prediction for a single feature vector."""
        self.inference_total += 1
        try:
            if self.model is None:
                raise ValueError("Model must be trained before prediction")

            row = self._to_feature_matrix([feature_vector])
            raw_score = float(self.model.decision_function(row)[0])
            is_anomaly = bool(self.model.predict(row)[0] == -1)

            severity = self._severity_from_score(raw_score, is_anomaly)
            if is_anomaly:
                self.anomalies_total += 1

            return {
                "window_id": feature_vector.window_id,
                "raw_score": round(raw_score, 6),
                "anomaly_score": round(normalize_isolation_forest_score(raw_score), 6),
                "is_anomaly": is_anomaly,
                "severity": severity,
                "model_version": self.model_version,
            }
        except Exception:
            self.inference_errors_total += 1
            raise

    def predict_batch(
        self, feature_vectors: list[FeatureVector]
    ) -> list[dict[str, Any]]:
        """Return structured predictions for a batch of feature vectors."""
        self.inference_total += len(feature_vectors)
        try:
            if self.model is None:
                raise ValueError("Model must be trained before prediction")

            matrix = self._to_feature_matrix(feature_vectors)
            scores = self.model.decision_function(matrix)
            predictions = self.model.predict(matrix)
            results: list[dict[str, Any]] = []

            for feature_vector, score, prediction in zip(
                feature_vectors, scores, predictions
            ):
                raw_score = float(score)
                is_anomaly = bool(prediction == -1)
                if is_anomaly:
                    self.anomalies_total += 1
                results.append(
                    {
                        "window_id": feature_vector.window_id,
                        "raw_score": round(raw_score, 6),
                        "anomaly_score": round(
                            normalize_isolation_forest_score(raw_score), 6
                        ),
                        "is_anomaly": is_anomaly,
                        "severity": self._severity_from_score(raw_score, is_anomaly),
                        "model_version": self.model_version,
                    }
                )

            return results
        except Exception:
            self.inference_errors_total += len(feature_vectors) or 1
            raise

    def save_model(self, path: str | Path) -> None:
        """Persist the trained model and metadata to disk."""
        if self.model is None:
            raise ValueError("No trained model available to save")

        model_path = Path(path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": self.model,
                "model_version": self.model_version,
                "feature_columns": FEATURE_COLUMNS,
                "training_samples": self.training_samples,
            },
            model_path,
        )
        self.model_path = model_path

    @classmethod
    def load_model(cls, path: str | Path) -> IsolationForestAnomalyDetector:
        """Load a previously serialized detector from disk."""
        model_path = Path(path)
        if not model_path.exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")

        payload = joblib.load(model_path)
        if not isinstance(payload, dict) or payload.get("model") is None:
            raise ValueError(f"Invalid Isolation Forest artifact: {model_path}")
        detector = cls()
        detector.model = payload.get("model")
        detector.model_version = payload.get("model_version", detector.model_version)
        detector.training_samples = payload.get("training_samples", 0)
        detector.model_path = model_path
        return detector

    def get_health(self, model_path: str | Path | None = None) -> dict[str, Any]:
        """Return bounded operator-visible model lifecycle state."""
        artifact_path = Path(model_path) if model_path is not None else self.model_path
        model_age_seconds: float | None = None
        if artifact_path is not None and artifact_path.exists():
            try:
                model_age_seconds = max(
                    0.0, time.time() - artifact_path.stat().st_mtime
                )
            except OSError:
                model_age_seconds = None

        return {
            "model_loaded": self.model is not None,
            "model_version": self.model_version if self.model is not None else None,
            "model_age_seconds": model_age_seconds,
            "artifact_path": str(artifact_path) if artifact_path is not None else None,
            "inference_total": self.inference_total,
            "inference_errors_total": self.inference_errors_total,
            "anomalies_total": self.anomalies_total,
        }

    def _to_feature_matrix(
        self, feature_vectors: list[FeatureVector]
    ) -> list[list[float]]:
        """Convert feature vectors into a numerical matrix for the sklearn model."""
        rows: list[list[float]] = []
        for vector in feature_vectors:
            row: list[float] = []
            for column in FEATURE_COLUMNS:
                value = self._get_feature_value(vector, column)
                row.append(float(value))
            rows.append(row)
        return rows

    def _get_feature_value(self, feature_vector: FeatureVector, column: str) -> float:
        """Safely read a feature value from the feature vector payload."""
        if isinstance(feature_vector.features, dict):
            value = feature_vector.features.get(column)
            if value is not None:
                return float(value)

        if feature_vector.feature_names and feature_vector.feature_array:
            if column in feature_vector.feature_names:
                index = feature_vector.feature_names.index(column)
                return float(feature_vector.feature_array[index])

        fallback_map = {
            "log_count": getattr(feature_vector, "log_count", 0),
            "info_count": getattr(feature_vector, "features", {}).get("info_count", 0),
            "warning_count": getattr(feature_vector, "features", {}).get(
                "warning_count", 0
            ),
            "error_count": getattr(feature_vector, "error_count", 0),
            "error_ratio": getattr(feature_vector, "features", {}).get(
                "error_ratio", 0.0
            ),
            "active_services": getattr(feature_vector, "features", {}).get(
                "active_services", 0.0
            ),
            "unique_templates": getattr(feature_vector, "unique_templates", 0),
            "dominant_service_count": getattr(feature_vector, "features", {}).get(
                "dominant_service_count", 0.0
            ),
            "dominant_template_count": getattr(feature_vector, "features", {}).get(
                "dominant_template_count", 0.0
            ),
            "logs_per_second": getattr(feature_vector, "logs_per_second", 0.0),
            "avg_logs_per_minute": getattr(feature_vector, "features", {}).get(
                "avg_logs_per_minute", 0.0
            ),
            "burst_indicator": getattr(feature_vector, "features", {}).get(
                "burst_indicator", 0.0
            ),
        }
        return float(fallback_map.get(column, 0.0))

    @staticmethod
    def _severity_from_score(score: float, is_anomaly: bool) -> str:
        """Translate the model score into a simple deterministic severity label."""
        if not is_anomaly:
            return "normal"
        if score <= -0.5:
            return "high"
        if score <= -0.2:
            return "medium"
        return "low"

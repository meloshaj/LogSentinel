"""Train and persist an IsolationForest anomaly detector from sample feature vectors."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

from ..models import FeatureVector
from .anomaly_detector import IsolationForestAnomalyDetector


def build_sample_feature_vectors() -> list[FeatureVector]:
    """Create a small sample dataset from synthetic windows."""
    base_time = datetime.now(timezone.utc)
    vectors: list[FeatureVector] = []
    for index in range(6):
        log_count = 20 + index * 2
        error_count = 1 if index < 3 else 8 + index
        vectors.append(
            FeatureVector(
                window_id=f"sample-window-{index}",
                timestamp=base_time + timedelta(minutes=index),
                window_start=base_time + timedelta(minutes=index),
                window_end=base_time + timedelta(minutes=index + 1),
                log_count=log_count,
                unique_templates=3 + (index % 2),
                error_count=error_count,
                warning_count=0,
                template_frequencies={"template-1": 0.5},
                template_entropy=0.1,
                service_distribution={"auth": log_count},
                logs_per_second=float(log_count) / 60.0,
                feature_array=[float(log_count), float(error_count), float(3 + (index % 2))],
                feature_names=["log_count", "error_count", "unique_templates"],
                features={
                    "log_count": float(log_count),
                    "info_count": float(log_count - error_count),
                    "warning_count": 0.0,
                    "error_count": float(error_count),
                    "error_ratio": float(error_count / max(log_count, 1)),
                    "active_services": 1.0,
                    "unique_templates": float(3 + (index % 2)),
                    "dominant_service_count": float(log_count),
                    "dominant_template_count": float(3 + (index % 2)),
                    "logs_per_second": float(log_count) / 60.0,
                    "avg_logs_per_minute": float(log_count),
                    "burst_indicator": 0.0,
                    "dominant_service": "auth",
                    "dominant_template": "template-1",
                },
            )
        )
    return vectors


def main() -> None:
    """Train the detector and save it to the models directory."""
    vectors = build_sample_feature_vectors()
    detector = IsolationForestAnomalyDetector(random_state=42)
    detector.train(vectors)

    output_path = Path(__file__).resolve().parents[2] / "models" / "isolation_forest.pkl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    detector.save_model(output_path)
    print(f"Saved model to {output_path}")


if __name__ == "__main__":
    main()

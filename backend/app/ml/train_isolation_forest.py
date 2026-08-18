"""Train and persist an IsolationForest anomaly detector from sample feature vectors.

The baseline training data models realistic steady-state traffic:
  - 30-40 logs per 10s window
  - 0-1 errors per window (< 3% error ratio)
  - 3-5 unique templates
  - 1-4 active services
  - Stable logs_per_second around 3-4

Any window with a significantly higher error rate, burst of logs,
or novel template explosion will be flagged as anomalous.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import FeatureVector
from .anomaly_detector import IsolationForestAnomalyDetector


def build_sample_feature_vectors() -> list[FeatureVector]:
    """Create a realistic steady-state baseline dataset.

    Generates 200 samples representing normal healthy microservice traffic
    with very low error rates (0-3%) and stable throughput.
    """
    rng = random.Random(42)
    base_time = datetime.now(timezone.utc)
    vectors: list[FeatureVector] = []

    for index in range(200):
        # Normal steady-state: 30-45 logs per 10s window
        log_count = rng.randint(30, 45)
        # Normal error rate: 0-1 errors (< 3%)
        error_count = rng.choices([0, 0, 0, 0, 1], k=1)[0]
        warning_count = rng.choices([0, 0, 1, 1, 2], k=1)[0]
        info_count = log_count - error_count - warning_count
        unique_templates = rng.randint(3, 5)
        active_services = rng.randint(1, 4)
        logs_per_second = float(log_count) / 10.0

        vectors.append(
            FeatureVector(
                window_id=f"sample-window-{index}",
                timestamp=base_time + timedelta(seconds=index * 10),
                window_start=base_time + timedelta(seconds=index * 10),
                window_end=base_time + timedelta(seconds=(index + 1) * 10),
                log_count=log_count,
                unique_templates=unique_templates,
                error_count=error_count,
                warning_count=warning_count,
                template_frequencies={"template-1": 0.5},
                template_entropy=rng.uniform(0.05, 0.3),
                service_distribution={"api-gateway": log_count},
                logs_per_second=logs_per_second,
                feature_array=[float(log_count), float(error_count), float(unique_templates)],
                feature_names=["log_count", "error_count", "unique_templates"],
                features={
                    "log_count": float(log_count),
                    "info_count": float(info_count),
                    "warning_count": float(warning_count),
                    "error_count": float(error_count),
                    "error_ratio": float(error_count) / max(log_count, 1),
                    "active_services": float(active_services),
                    "unique_templates": float(unique_templates),
                    "dominant_service_count": float(log_count),
                    "dominant_template_count": float(unique_templates),
                    "logs_per_second": logs_per_second,
                    "avg_logs_per_minute": float(log_count) * 6.0,
                    "burst_indicator": 0.0,
                    "dominant_service": "api-gateway",
                    "dominant_template": "template-1",
                },
            )
        )
    return vectors


def main() -> None:
    """Train the detector and save it to the models directory."""
    vectors = build_sample_feature_vectors()
    detector = IsolationForestAnomalyDetector(random_state=42, contamination=0.05)
    detector.train(vectors)

    output_path = Path(__file__).resolve().parents[2] / "models" / "isolation_forest.pkl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    detector.save_model(output_path)
    print(f"Saved model to {output_path} (trained on {len(vectors)} samples)")


if __name__ == "__main__":
    main()

"""Build synthetic healthy feature windows for local demo-model training.

All vectors are generated locally from synthetic logs and the production
feature extractor. They are demonstration training data, never production
observations.
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import FeatureVector, LogWindow, ParsedLog
from .anomaly_detector import (
    FEATURE_COLUMNS,
    IsolationForestAnomalyDetector,
    get_canonical_model_path,
)
from .feature_extractor import SlidingWindowFeatureExtractor, WindowConfig

SYNTHETIC_DATASET_ID = "logsentinel-synthetic-healthy-demo-v2"
_HEALTHY_SERVICES = (
    "auth-service",
    "order-service",
    "payment-gateway",
    "database-service",
)
_HEALTHY_TEMPLATE_IDS = ("30", "31", "32")
_NORMAL_LOG_RATES_PER_SECOND = (1.1, 1.5, 2.0, 2.2, 2.4, 2.5)
_RECOVERY_SERVICES = ("order-service", "payment-gateway")


def build_sample_feature_vectors(
    sample_count: int = 200, *, seed: int = 42
) -> list[FeatureVector]:
    """Create a healthy-only baseline using the actual sliding-window logic.

    The steady profile spans the observed 11-25 logs per 10-second window.
    One fifth covers the observed healthy, low-volume recovery activity:
    9-14 INFO logs across two services with one stable template. Both profiles pass
    synthetic ``ParsedLog`` records through the production 10-second window,
    5-second-stride extractor, so overlapping windows and all derived values
    remain internally consistent.

    A separate seed creates independent healthy validation fixtures. The
    provenance marker is stored outside the 12 numeric model columns.
    """
    if sample_count < 1:
        raise ValueError("sample_count must be at least one")

    recovery_count = max(1, sample_count // 5)
    steady_count = sample_count - recovery_count
    vectors = _build_steady_healthy_vectors(steady_count, seed=seed)
    vectors.extend(
        _build_recovery_healthy_vectors(recovery_count, seed=seed + 100_003)
    )
    random.Random(seed + 7).shuffle(vectors)
    return vectors


def _new_extractor() -> SlidingWindowFeatureExtractor:
    return SlidingWindowFeatureExtractor(
        WindowConfig(window_size_seconds=10, stride_seconds=5, min_logs_per_window=1)
    )


def _mark_synthetic(vector: FeatureVector, scenario: str) -> FeatureVector:
    vector.features["synthetic_training_data"] = True
    vector.features["training_dataset_id"] = SYNTHETIC_DATASET_ID
    vector.features["synthetic_scenario"] = scenario
    vector.feature_names = FEATURE_COLUMNS.copy()
    vector.feature_array = [float(vector.features[name]) for name in FEATURE_COLUMNS]
    return vector


def _build_steady_healthy_vectors(
    sample_count: int, *, seed: int
) -> list[FeatureVector]:
    rng = random.Random(seed)
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=seed % 365
    )
    vectors: list[FeatureVector] = []
    session_index = 0

    while len(vectors) < sample_count:
        extractor = _new_extractor()
        session_start = base_time + timedelta(
            seconds=session_index * 120 + 5.0 + rng.uniform(-0.4, 0.4)
        )
        rate = rng.choice(_NORMAL_LOG_RATES_PER_SECOND)
        elapsed = rng.uniform(0.05, 0.35)
        log_index = 0
        last_timestamp = session_start

        while elapsed < 50.0:
            elapsed += rng.uniform(0.9, 1.1) / rate
            if elapsed >= 50.0:
                break

            service = _HEALTHY_SERVICES[
                (log_index + seed + session_index) % len(_HEALTHY_SERVICES)
            ]
            template_id = rng.choices(
                _HEALTHY_TEMPLATE_IDS, weights=(0.5, 0.25, 0.25), k=1
            )[0]
            level_roll = rng.random()
            level = (
                "error"
                if level_roll < 0.003
                else "warning"
                if level_roll < 0.043
                else "info"
            )
            event_id = f"synthetic-demo-{seed}-steady-{session_index}-{log_index}"
            last_timestamp = session_start + timedelta(seconds=elapsed)
            extractor.add_log(
                ParsedLog(
                    id=event_id,
                    event_id=event_id,
                    timestamp=last_timestamp,
                    service=service,
                    level=level,
                    raw_message=f"Synthetic healthy demo {level} template {template_id}",
                    template_id=template_id,
                    template_text=f"Synthetic healthy demo template {template_id}",
                    tenant_id="synthetic-demo-training",
                    owner_user_id=1,
                    source="synthetic-demo-training",
                    environment="local-validation",
                    metadata={
                        "synthetic_training_data": True,
                        "training_dataset_id": SYNTHETIC_DATASET_ID,
                        "scenario": "healthy_steady_state",
                    },
                )
            )
            log_index += 1

        windows = extractor.get_pending_windows(
            current_time=last_timestamp + timedelta(seconds=1)
        )
        for window in windows:
            vector = extractor.extract_features(window)
            # Omit only incomplete startup/tail windows below the observed
            # account range and synthetic jitter overshoots above it. The
            # retained population matches the existing account's 9-25 logs
            # per 10-second window rather than teaching artificial edge rows.
            if 9 <= vector.log_count <= 25:
                vectors.append(_mark_synthetic(vector, "healthy_steady_state"))
            if len(vectors) == sample_count:
                break
        session_index += 1

    return vectors


def _build_recovery_healthy_vectors(
    sample_count: int, *, seed: int
) -> list[FeatureVector]:
    rng = random.Random(seed)
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=seed % 365
    )
    vectors: list[FeatureVector] = []
    session_index = 0

    while len(vectors) < sample_count:
        extractor = _new_extractor()
        session_start = base_time + timedelta(
            seconds=session_index * 60 + 5.3 + rng.uniform(-0.2, 0.2)
        )
        last_timestamp = session_start

        log_count = rng.choice((9, 10, 11, 12, 13, 14))
        recovery_logs: list[ParsedLog] = []
        for log_index in range(log_count):
            event_id = f"synthetic-demo-{seed}-recovery-{session_index}-{log_index}"
            last_timestamp = session_start + timedelta(seconds=log_index * 0.35)
            service = _RECOVERY_SERVICES[log_index % len(_RECOVERY_SERVICES)]
            log = ParsedLog(
                id=event_id,
                event_id=event_id,
                timestamp=last_timestamp,
                service=service,
                level="info",
                raw_message="Synthetic healthy demo recovery completed",
                template_id="36",
                template_text="Synthetic healthy demo recovery completed",
                tenant_id="synthetic-demo-training",
                owner_user_id=1,
                source="synthetic-demo-training",
                environment="local-validation",
                metadata={
                    "synthetic_training_data": True,
                    "training_dataset_id": SYNTHETIC_DATASET_ID,
                    "scenario": "healthy_recovery_profile",
                },
            )
            recovery_logs.append(log)
            extractor.add_log(log)

        # The existing production recovery windows are sparse (9-14 logs),
        # so represent one closed 10-second feature window at a time. The
        # same production extractor computes every field and derived metric.
        window = LogWindow(
            window_id=f"synthetic-recovery-{seed}-{session_index}",
            start_time=session_start,
            end_time=session_start + timedelta(seconds=10),
            logs=recovery_logs,
        )
        vectors.append(
            _mark_synthetic(
                extractor.extract_features(window), "healthy_recovery_profile"
            )
        )
        session_index += 1

    return vectors


def main() -> None:
    """Train a local candidate without overwriting the canonical artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New candidate artifact path; an existing file is never overwritten",
    )
    args = parser.parse_args()
    output_path = args.output.resolve()
    if output_path == get_canonical_model_path().resolve():
        parser.error("refusing to overwrite the canonical model artifact")
    if output_path.exists():
        parser.error(f"candidate already exists: {output_path}")

    vectors = build_sample_feature_vectors()
    detector = IsolationForestAnomalyDetector(random_state=42, contamination=0.05)
    detector.train(vectors)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    detector.save_model(
        output_path,
        artifact_metadata={
            "feature_schema_version": "logsentinel-feature-schema-v1",
            "training_data_kind": "synthetic_demo_healthy_only",
            "training_dataset_id": SYNTHETIC_DATASET_ID,
            "training_samples": len(vectors),
        },
    )
    print(f"Saved local candidate to {output_path} ({len(vectors)} synthetic windows)")


if __name__ == "__main__":
    main()

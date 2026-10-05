"""Offline validation of a synthetic healthy demo model against demo scenarios.

This script is local-only. It reads the checked-in production-window snapshot,
creates synthetic validation logs through the production extractor, and never
connects to LogSentinel or writes to production. A candidate artifact is saved
only after the explicit validation gates pass and is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import statistics
import sys
import time
import platform
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import sklearn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "demo" / "jury"))

from app.ml.anomaly_detector import (  # noqa: E402
    FEATURE_COLUMNS,
    IsolationForestAnomalyDetector,
    get_canonical_model_path,
)
from app.ml.feature_extractor import SlidingWindowFeatureExtractor, WindowConfig  # noqa: E402
from app.ml.train_isolation_forest import (  # noqa: E402
    SYNTHETIC_DATASET_ID,
    build_sample_feature_vectors,
)
from app.models import FeatureVector, ParsedLog  # noqa: E402
from jury_100_logs import event  # noqa: E402

SCHEMA_VERSION = "logsentinel-feature-schema-v1"
PRODUCTION_SNAPSHOT = ROOT / "demo" / "jury" / "fixtures" / "production_100_run_windows.json"
DEFAULT_CANDIDATE = ROOT / "demo" / "jury" / "artifacts" / "logsentinel-owner7-iforest-demo-candidate.joblib"


def _feature_vector(window_id: str, values: dict[str, Any], *, tenant: str = "logsentinel", owner: int = 7) -> FeatureVector:
    numeric = {name: float(values[name]) for name in FEATURE_COLUMNS}
    return FeatureVector(
        window_id=window_id,
        timestamp=datetime.now(timezone.utc),
        tenant_id=tenant,
        owner_user_id=owner,
        log_count=int(numeric["log_count"]),
        unique_templates=int(numeric["unique_templates"]),
        error_count=int(numeric["error_count"]),
        warning_count=int(numeric["warning_count"]),
        logs_per_second=numeric["logs_per_second"],
        feature_names=list(FEATURE_COLUMNS),
        feature_array=[numeric[column] for column in FEATURE_COLUMNS],
        features=numeric,
    )


def _production_vectors() -> list[tuple[str, FeatureVector]]:
    snapshot = json.loads(PRODUCTION_SNAPSHOT.read_text(encoding="utf-8"))
    if snapshot.get("feature_schema_version") != SCHEMA_VERSION:
        raise ValueError("production snapshot schema version mismatch")
    if snapshot.get("feature_columns") != FEATURE_COLUMNS:
        raise ValueError("production snapshot feature order mismatch")
    return [
        (
            row["phase_class"],
            _feature_vector(row["window_id"], row["features"]),
        )
        for row in snapshot["windows"]
    ]


def _phase_vectors(phase: str, count: int, *, seed: int) -> list[FeatureVector]:
    extractor = SlidingWindowFeatureExtractor(
        WindowConfig(window_size_seconds=10, stride_seconds=5, min_logs_per_window=1)
    )
    # The normal phase in the original run straddled the extractor's 10s
    # boundary; anchoring at +6.5s reproduces the observed 11/25/24 overlap
    # pattern. The outage phase starts just after a boundary so its complete
    # 25-record failure window remains observable.
    start_offset = 6.5 if phase == "normal" else 0.2
    base = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc) + timedelta(
        seconds=start_offset
    )
    last = base
    for index in range(count):
        service, level_number, message, metadata = event(phase, index, f"offline-{seed}")
        timestamp = base + timedelta(seconds=index * 0.35)
        last = timestamp
        level = logging.getLevelName(level_number).lower()
        if phase == "normal":
            template_id = {
                "auth-service": "30",
                "order-service": "30",
                "payment-gateway": "32",
                "database-service": "31",
            }[service]
        elif phase == "auth_failures":
            template_id = "33"
        elif phase == "payment_outage":
            template_id = "34" if level == "critical" else "35"
        else:
            template_id = "36"
        event_id = str(metadata["event_id"])
        extractor.add_log(
            ParsedLog(
                id=event_id,
                event_id=event_id,
                timestamp=timestamp,
                service=service,
                level=level,
                raw_message=message,
                template_id=template_id,
                template_text=message,
                tenant_id="logsentinel",
                owner_user_id=7,
                source="offline-demo-validation",
                environment="synthetic-demo-validation",
                metadata={**metadata, "synthetic_validation_data": True},
            )
        )
    windows = extractor.get_pending_windows(current_time=last + timedelta(seconds=20))
    vectors = [extractor.extract_features(window) for window in windows if window.logs]
    for index, vector in enumerate(vectors):
        vector.window_id = f"synthetic-{phase}-{seed}-{index}"
    return vectors


def _matrix(vector: FeatureVector) -> list[float]:
    return [float(vector.features[name]) for name in FEATURE_COLUMNS]


def _distribution_report(groups: dict[str, list[FeatureVector]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for group, vectors in groups.items():
        if not vectors:
            continue
        result[group] = {}
        for column in FEATURE_COLUMNS:
            values = [float(vector.features[column]) for vector in vectors]
            result[group][column] = {
                "min": round(min(values), 4),
                "median": round(statistics.median(values), 4),
                "max": round(max(values), 4),
            }
    return result


def _prediction_rows(detector: IsolationForestAnomalyDetector, vectors: list[FeatureVector]) -> list[dict[str, Any]]:
    return detector.predict_batch(vectors)


def _scored_feature_rows(
    detector: IsolationForestAnomalyDetector, vectors: list[FeatureVector]
) -> list[dict[str, Any]]:
    predictions = detector.predict_batch(vectors)
    return [
        {
            **prediction,
            "feature_values": {
                column: round(float(vector.features[column]), 4)
                for column in FEATURE_COLUMNS
            },
        }
        for vector, prediction in zip(vectors, predictions)
    ]


def _save_candidate(detector: IsolationForestAnomalyDetector, path: Path) -> str:
    path = path.resolve()
    if path == get_canonical_model_path().resolve():
        raise ValueError("refusing to write to the canonical application model path")
    if path.exists():
        raise FileExistsError(f"candidate already exists; refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    detector.save_model(
        path,
        artifact_metadata={
            "tenant_id": "logsentinel",
            "owner_user_id": 7,
            "feature_schema_version": SCHEMA_VERSION,
            "feature_columns": list(FEATURE_COLUMNS),
            "training_data_kind": "synthetic_demo_healthy_only",
            "training_dataset_id": SYNTHETIC_DATASET_ID,
            "training_samples": detector.training_samples,
            "scikit_learn_version": sklearn.__version__,
            "promotion_state": "local_candidate_not_registered",
        },
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = joblib.load(path)
    if payload.get("feature_columns") != FEATURE_COLUMNS:
        raise ValueError("serialized artifact has incorrect feature order")
    if payload.get("artifact_metadata", {}).get("feature_schema_version") != SCHEMA_VERSION:
        raise ValueError("serialized artifact schema metadata mismatch")
    loaded = IsolationForestAnomalyDetector.load_model(path)
    if loaded.model is None or getattr(loaded.model, "n_features_in_", None) != len(FEATURE_COLUMNS):
        raise ValueError("serialized candidate failed local load/schema check")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-path", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument(
        "--report-path",
        type=Path,
        default=ROOT / "demo" / "jury" / "fixtures" / "offline_model_validation_20260930.json",
    )
    parser.add_argument(
        "--write-candidate",
        action="store_true",
        help="save a new local-only candidate, but only after all validation gates pass",
    )
    args = parser.parse_args()
    if args.report_path.exists():
        parser.error(f"refusing to overwrite validation report: {args.report_path}")

    started = time.perf_counter()
    train = build_sample_feature_vectors(200, seed=42)
    print(f"Built 200 training windows in {time.perf_counter() - started:.1f}s", flush=True)
    healthy = build_sample_feature_vectors(200, seed=20260930)
    print(f"Built 200 independent healthy windows in {time.perf_counter() - started:.1f}s", flush=True)
    phase_data = {
        "demo_normal": _phase_vectors("normal", 35, seed=1),
        "auth_failures": _phase_vectors("auth_failures", 25, seed=2),
        "payment_outage": _phase_vectors("payment_outage", 25, seed=3),
        "recovery": _phase_vectors("recovery", 15, seed=4),
    }
    production = _production_vectors()
    print(f"Built phase fixtures and loaded production snapshot in {time.perf_counter() - started:.1f}s", flush=True)

    detector = IsolationForestAnomalyDetector(random_state=42, contamination=0.05)
    detector.train(train)
    print(f"Trained model and starting batched predictions in {time.perf_counter() - started:.1f}s", flush=True)
    healthy_predictions = _prediction_rows(detector, healthy)
    phase_predictions = {
        phase: _scored_feature_rows(detector, vectors)
        for phase, vectors in phase_data.items()
    }
    production_predictions = [
        {
            "phase_class": phase_class,
            **_prediction_rows(detector, [vector])[0],
        }
        for phase_class, vector in production
    ]

    fp_count = sum(bool(row["is_anomaly"]) for row in healthy_predictions)
    pure_payment_fixture = [
        row
        for row in phase_predictions["payment_outage"]
        if row["window_id"] and next(
            vector
            for vector in phase_data["payment_outage"]
            if vector.window_id == row["window_id"]
        ).features["error_ratio"] >= 0.999
    ]
    detected_pure_outage = sum(bool(row["is_anomaly"]) for row in pure_payment_fixture)
    production_pure_outage = [
        row for row in production_predictions
        if row["phase_class"] == "payment_outage"
    ]
    production_clean = [
        row for row in production_predictions
        if row["phase_class"] in {"normal", "recovery"}
    ]
    clean_production_fp = sum(bool(row["is_anomaly"]) for row in production_clean)
    pass_validation = (
        fp_count <= 10
        and bool(pure_payment_fixture)
        and detected_pure_outage == len(pure_payment_fixture)
        and len(production_pure_outage) == 1
        and bool(production_pure_outage[0]["is_anomaly"])
        and clean_production_fp == 0
    )

    groups: dict[str, list[FeatureVector]] = {
        "train_healthy": train,
        "independent_healthy_validation": healthy,
        **phase_data,
    }
    for phase_class, vector in production:
        groups.setdefault(f"production_{phase_class}", []).append(vector)
    report = {
        "validation": "PASS" if pass_validation else "FAIL",
        "gates": {
            "healthy_false_positives_max_10_of_200": fp_count <= 10,
            "synthetic_pure_payment_outage_all_detected": bool(pure_payment_fixture)
            and detected_pure_outage == len(pure_payment_fixture),
            "persisted_pure_payment_outage_detected": len(production_pure_outage) == 1
            and bool(production_pure_outage[0]["is_anomaly"]),
            "persisted_clean_normal_and_recovery_no_false_positives": clean_production_fp == 0,
        },
        "training": {
            "count": len(train),
            "synthetic_dataset_id": SYNTHETIC_DATASET_ID,
            "contamination": 0.05,
            "scikit_learn_version": sklearn.__version__,
            "joblib_version": joblib.__version__,
            "python_version": platform.python_version(),
            "feature_schema_version": SCHEMA_VERSION,
            "feature_columns": FEATURE_COLUMNS,
        },
        "healthy_validation": {
            "count": len(healthy_predictions),
            "false_positives": fp_count,
            "false_positive_rate": round(fp_count / len(healthy_predictions), 6),
            "false_positive_windows": [
                {
                    **prediction,
                    "scenario": vector.features.get("synthetic_scenario"),
                    "feature_values": {
                        column: round(float(vector.features[column]), 4)
                        for column in FEATURE_COLUMNS
                    },
                }
                for vector, prediction in zip(healthy, healthy_predictions)
                if prediction["is_anomaly"]
            ],
        },
        "phase_predictions": phase_predictions,
        "production_100_run_predictions": production_predictions,
        "feature_distributions_min_median_max": _distribution_report(groups),
        "candidate": None,
    }

    if args.write_candidate and pass_validation:
        report["candidate"] = {
            "path": str(args.candidate_path.resolve()),
            "sha256": _save_candidate(detector, args.candidate_path),
            "schema_version": SCHEMA_VERSION,
            "feature_columns": list(FEATURE_COLUMNS),
            "tenant_id": "logsentinel",
            "owner_user_id": 7,
            "registration": "not performed",
            "activation": "not performed",
        }
    elif args.write_candidate:
        report["candidate"] = {
            "written": False,
            "reason": "validation gates failed",
        }

    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"Validation {report['validation']}; healthy false positives "
        f"{fp_count}/{len(healthy_predictions)}; pure synthetic payment outage "
        f"detections {detected_pure_outage}/{len(pure_payment_fixture)}; "
        f"clean persisted false positives {clean_production_fp}; "
        f"report: {args.report_path.resolve()}"
    )
    return 0 if pass_validation else 2


if __name__ == "__main__":
    raise SystemExit(main())

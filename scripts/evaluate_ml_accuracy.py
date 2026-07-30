#!/usr/bin/env python3
"""LogSentinel ML Accuracy Evaluation Engine.

Benchmarks Isolation Forest classification performance against ground-truth
labeled log datasets.  The script is fully self-contained:

1. Generates 5,000 labeled synthetic logs (85 % normal / 15 % anomalous).
2. Passes them through Drain3 template parsing → sliding-window feature
   extraction (matching the production feature extractor).
3. Trains an Isolation Forest model on the *normal-only* portion and scores
   the full dataset.
4. Computes classification metrics (precision, recall, F1, FPR, ROC-AUC)
   and performs a threshold-sweep analysis.
5. Exports ``ml_evaluation_results.json`` and prints a Markdown summary.

Usage::

    python scripts/evaluate_ml_accuracy.py [OPTIONS]

Requires: drain3, scikit-learn, numpy (all declared in backend/requirements.txt).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import numpy as np
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────

DEFAULT_TOTAL_RECORDS = 5_000
NORMAL_RATIO = 0.85
ANOMALY_RATIO = 0.15
DEFAULT_OUTPUT_PATH = Path("ml_evaluation_results.json")
DEFAULT_SEED = 42
DEFAULT_CONTAMINATION = 0.15
DEFAULT_WINDOW_SIZE_SECONDS = 60
DEFAULT_STRIDE_SECONDS = 30

# 12-feature vector matching backend/app/ml/anomaly_detector.FEATURE_COLUMNS
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

THRESHOLD_SWEEP_RANGE = (-0.80, 0.01, 0.025)  # start, stop (exclusive), step


# ──────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────

@dataclass
class LabeledLogEntry:
    """A single synthetic log with ground-truth anomaly flag."""
    index: int
    timestamp: datetime
    service: str
    level: str
    message: str
    raw: str
    is_anomaly: bool
    anomaly_type: Optional[str]


@dataclass
class WindowLabel:
    """Aggregated ground-truth for a log window."""
    window_id: str
    total_logs: int
    anomaly_count: int
    normal_count: int
    is_anomaly: bool
    dominant_anomaly_type: Optional[str]


@dataclass
class ConfusionMatrix:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1_score(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def fpr(self) -> float:
        denom = self.fp + self.tn
        return self.fp / denom if denom else 0.0

    @property
    def accuracy(self) -> float:
        total = self.tp + self.fp + self.tn + self.fn
        return (self.tp + self.tn) / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "precision": round(self.precision, 6),
            "recall": round(self.recall, 6),
            "f1_score": round(self.f1_score, 6),
            "fpr": round(self.fpr, 6),
            "accuracy": round(self.accuracy, 6),
        }


@dataclass
class ThresholdResult:
    threshold: float
    cm: ConfusionMatrix
    roc_auc: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": round(self.threshold, 4),
            **self.cm.to_dict(),
            "roc_auc": round(self.roc_auc, 6),
        }


@dataclass
class EvaluationResult:
    """Full evaluation payload."""
    run_id: str
    started_at: str
    finished_at: str
    dataset: dict[str, Any]
    drain3_stats: dict[str, Any]
    windows_generated: int
    model_config: dict[str, Any]
    default_threshold: ThresholdResult
    optimal_threshold: ThresholdResult
    threshold_sweep: list[dict[str, Any]]
    per_anomaly_type: dict[str, dict[str, Any]]
    timing: dict[str, float]


# ──────────────────────────────────────────────────────────────
# Drain3 In-Memory Config
# ──────────────────────────────────────────────────────────────

DRAIN3_INI = """\
[MINER]
engine = Drain

[PROFILING]
enabled = False
report_sec = 60

[SNAPSHOT]
snapshot_interval_minutes = 0
compress_state = True

[DRAIN]
sim_th = 0.4
depth = 4
max_children = 100
extra_delimiters = []
parametrize_numeric_tokens = True

[MASKING]
mask_prefix = <
mask_suffix = >
parameter_extraction_cache_capacity = 3000
masking = [{"regex_pattern": "\\\\b\\\\d{4}-\\\\d{2}-\\\\d{2}[T ]\\\\d{2}:\\\\d{2}:\\\\d{2}(?:\\\\.\\\\d+)?(?:Z|[+-]\\\\d{2}:?\\\\d{2})?\\\\b", "mask_with": "TIMESTAMP"}, {"regex_pattern": "\\\\b\\\\d{4}-\\\\d{2}-\\\\d{2}\\\\b", "mask_with": "DATE"}, {"regex_pattern": "\\\\b\\\\d{2}:\\\\d{2}:\\\\d{2}(?:\\\\.\\\\d+)?\\\\b", "mask_with": "TIME"}, {"regex_pattern": "\\\\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\\\\b", "mask_with": "UUID"}, {"regex_pattern": "\\\\b(?:\\\\d{1,3}\\\\.){3}\\\\d{1,3}\\\\b", "mask_with": "IP"}, {"regex_pattern": "(?i)\\\\b0x[0-9a-f]+\\\\b", "mask_with": "HEX"}, {"regex_pattern": "\\\\bport\\\\s+\\\\d{1,5}\\\\b", "mask_with": "PORT"}, {"regex_pattern": "(?<=:)\\\\d{1,5}\\\\b", "mask_with": "PORT"}, {"regex_pattern": "\\\\b[-+]?\\\\d+(?:\\\\.\\\\d+)?\\\\b", "mask_with": "NUM"}]
"""


def make_template_miner() -> TemplateMiner:
    """Create an in-memory Drain3 TemplateMiner (no file persistence)."""
    config = TemplateMinerConfig()
    # Write a temporary config so drain3 can load it
    tmp_ini = Path(__file__).parent / ".eval_drain3.ini"
    tmp_ini.write_text(DRAIN3_INI, encoding="utf-8")
    config.load(str(tmp_ini))
    config.parameter_extraction_cache_capacity = int(
        config.parameter_extraction_cache_capacity
    )
    miner = TemplateMiner(config=config)
    # Clean up temp file
    try:
        tmp_ini.unlink()
    except OSError:
        pass
    return miner


# ──────────────────────────────────────────────────────────────
# Synthetic Dataset Generator
# ──────────────────────────────────────────────────────────────

def generate_labeled_dataset(
    total: int = DEFAULT_TOTAL_RECORDS,
    seed: int = DEFAULT_SEED,
) -> list[LabeledLogEntry]:
    """Generate a deterministic labeled dataset with 85/15 normal/anomaly split.

    Normal logs (85 %):
        - HTTP 200/304 request completions
        - Routine DB query logs
        - Periodic background / cron tasks
        - Cache hit/miss events
        - Health check pings

    Anomalous logs (15 %):
        - SQL injection attempts
        - Auth brute-force events
        - Unhandled stack traces
        - Privilege escalation
        - Oversized / out-of-bounds payloads
    """
    rng = random.Random(seed)
    normal_count = int(total * NORMAL_RATIO)
    anomaly_count = total - normal_count
    records: list[LabeledLogEntry] = []

    services = ["api-gateway", "auth-service", "orders", "payments", "inventory-db"]
    regions = ["eu-central-1", "us-east-1", "us-west-2", "ap-southeast-1"]
    base_time = datetime(2026, 7, 30, 0, 0, 0, tzinfo=timezone.utc)

    # ── Normal log templates ─────────────────────────────────
    def normal_http_ok(idx: int) -> str:
        svc = rng.choice(services)
        status = rng.choice([200, 200, 200, 201, 204, 304])
        ms = rng.randint(5, 180)
        return (
            f"{svc} GET /api/v1/resource/{rng.randint(1,9999)} completed "
            f"status={status} duration={ms}ms client=10.0.{rng.randint(1,254)}.{rng.randint(1,254)}"
        ), svc, "info"

    def normal_db_query(idx: int) -> str:
        svc = rng.choice(["inventory-db", "orders", "payments"])
        ms = rng.randint(2, 60)
        rows = rng.randint(0, 500)
        return (
            f"{svc} SELECT query table='{rng.choice(['users','orders','inventory','sessions'])}' "
            f"rows={rows} duration={ms}ms plan=IndexScan"
        ), svc, "info"

    def normal_background(idx: int) -> str:
        svc = rng.choice(services)
        task = rng.choice(["cleanup_sessions", "sync_inventory", "rotate_logs", "send_digest"])
        return (
            f"{svc} background task {task} completed "
            f"items_processed={rng.randint(10, 5000)} duration={rng.randint(100, 8000)}ms"
        ), svc, "info"

    def normal_cache(idx: int) -> str:
        svc = rng.choice(services)
        hit = rng.choice(["true", "true", "true", "false"])
        return (
            f"{svc} cache lookup key=product:{rng.randint(1000,9999)} "
            f"hit={hit} ttl={rng.randint(60, 3600)}s latency={rng.randint(1, 8)}ms"
        ), svc, "info"

    def normal_health(idx: int) -> str:
        svc = rng.choice(services)
        region = rng.choice(regions)
        return (
            f"{svc} health check passed region={region} "
            f"latency={rng.randint(1, 15)}ms uptime={rng.randint(3600, 864000)}s"
        ), svc, "info"

    normal_generators = [normal_http_ok, normal_db_query, normal_background, normal_cache, normal_health]

    # ── Anomaly log templates (5 attack vectors) ─────────────
    def anomaly_sql_injection(idx: int) -> str:
        svc = rng.choice(["api-gateway", "orders"])
        payload = rng.choice([
            "' OR 1=1; DROP TABLE users; --",
            "'; DELETE FROM orders WHERE ''='",
            "' UNION SELECT password FROM admin; --",
            "1; EXEC xp_cmdshell('whoami'); --",
            "' OR ''='",
        ])
        return (
            f"{svc} blocked suspicious SQL input=\"{payload}\" "
            f"client_ip=203.0.113.{rng.randint(1,254)} path=/api/v1/search "
            f"param=query action=BLOCKED rule=SQL_INJECTION_GUARD"
        ), svc, "warning"

    def anomaly_brute_force(idx: int) -> str:
        svc = "auth-service"
        user = f"admin{rng.choice(['', '_root', '_system', '@company.com'])}"
        attempts = rng.randint(15, 200)
        return (
            f"{svc} brute force detected user={user} "
            f"failed_attempts={attempts} window=300s "
            f"source_ip=198.51.100.{rng.randint(1,254)} action=ACCOUNT_LOCKED "
            f"geo=CN method=POST path=/api/v1/auth/login"
        ), svc, "error"

    def anomaly_stack_trace(idx: int) -> str:
        svc = rng.choice(services)
        exc_type = rng.choice([
            "RuntimeError", "NullPointerException", "SegmentationFault",
            "OutOfMemoryError", "DatabaseConnectionError",
        ])
        line = rng.randint(30, 600)
        return (
            f"{svc} unhandled exception {exc_type}: critical failure in handler\n"
            f"Traceback (most recent call last):\n"
            f"  File \"/srv/app/handlers.py\", line {line}, in process_request\n"
            f"    result = await self.execute()\n"
            f"  File \"/srv/app/core/engine.py\", line {rng.randint(50, 400)}, in execute\n"
            f"    raise {exc_type}(\"internal state corrupted\")\n"
            f"{exc_type}: internal state corrupted request_id={uuid4().hex[:12]}"
        ), svc, "error"

    def anomaly_privilege_escalation(idx: int) -> str:
        svc = rng.choice(["auth-service", "api-gateway"])
        target_role = rng.choice(["superadmin", "root", "system_operator", "db_admin"])
        return (
            f"{svc} privilege escalation attempt user=user_{rng.randint(1000,9999)} "
            f"current_role=viewer target_role={target_role} "
            f"endpoint=/api/v1/admin/settings method=PUT "
            f"source_ip=192.0.2.{rng.randint(1,254)} action=DENIED "
            f"jwt_claims_modified=true original_exp={rng.randint(1700000000, 1800000000)}"
        ), svc, "error"

    def anomaly_oversized_payload(idx: int) -> str:
        svc = rng.choice(["api-gateway", "orders", "payments"])
        size_mb = round(rng.uniform(50, 500), 1)
        return (
            f"{svc} oversized request body detected size={size_mb}MB "
            f"limit=10MB path=/api/v1/upload "
            f"content_type={rng.choice(['application/octet-stream', 'multipart/form-data', 'text/plain'])} "
            f"client_ip=198.18.{rng.randint(0,255)}.{rng.randint(1,254)} action=REJECTED "
            f"potential_dos=true connection_rate={rng.randint(50, 500)}/min"
        ), svc, "warning"

    anomaly_generators = [
        ("sql_injection", anomaly_sql_injection),
        ("brute_force", anomaly_brute_force),
        ("stack_trace", anomaly_stack_trace),
        ("privilege_escalation", anomaly_privilege_escalation),
        ("oversized_payload", anomaly_oversized_payload),
    ]

    # ── Generate normal entries ──────────────────────────────
    for i in range(normal_count):
        ts = base_time + timedelta(seconds=i * 0.5 + rng.uniform(0, 0.3))
        gen = rng.choice(normal_generators)
        msg, svc, level = gen(i)
        records.append(LabeledLogEntry(
            index=i,
            timestamp=ts,
            service=svc,
            level=level,
            message=msg,
            raw=f"{ts.isoformat()} {level.upper()} {svc}: {msg}",
            is_anomaly=False,
            anomaly_type=None,
        ))

    # ── Generate anomalous entries ───────────────────────────
    # Equal distribution across 5 attack vectors (150 each for 750 total)
    per_type = anomaly_count // len(anomaly_generators)
    remainder = anomaly_count - per_type * len(anomaly_generators)

    offset = normal_count
    for type_idx, (anomaly_type, gen) in enumerate(anomaly_generators):
        count = per_type + (1 if type_idx < remainder else 0)
        for j in range(count):
            idx = offset
            offset += 1
            ts = base_time + timedelta(seconds=idx * 0.5 + rng.uniform(0, 0.3))
            msg, svc, level = gen(idx)
            records.append(LabeledLogEntry(
                index=idx,
                timestamp=ts,
                service=svc,
                level=level,
                message=msg,
                raw=f"{ts.isoformat()} {level.upper()} {svc}: {msg}",
                is_anomaly=True,
                anomaly_type=anomaly_type,
            ))

    # Shuffle to interleave normal and anomalous
    rng.shuffle(records)
    return records


# ──────────────────────────────────────────────────────────────
# Feature Extraction Pipeline
# ──────────────────────────────────────────────────────────────

@dataclass
class ParsedLogRecord:
    """Lightweight parsed log for windowing."""
    timestamp: datetime
    service: str
    level: str
    raw_message: str
    template_id: str
    template_text: str
    cluster_size: int
    change_type: str
    is_anomaly: bool
    anomaly_type: Optional[str]


def parse_with_drain3(
    miner: TemplateMiner,
    records: list[LabeledLogEntry],
) -> list[ParsedLogRecord]:
    """Parse every log record through Drain3 and return structured results."""
    parsed: list[ParsedLogRecord] = []
    for record in records:
        result = miner.add_log_message(record.message)
        parsed.append(ParsedLogRecord(
            timestamp=record.timestamp,
            service=record.service,
            level=record.level,
            raw_message=record.message,
            template_id=str(result["cluster_id"]),
            template_text=result["template_mined"],
            cluster_size=result["cluster_size"],
            change_type=result["change_type"],
            is_anomaly=record.is_anomaly,
            anomaly_type=record.anomaly_type,
        ))
    return parsed


@dataclass
class WindowFeatures:
    """Feature vector for a single log window with ground-truth label."""
    window_id: str
    features: dict[str, float]
    feature_array: list[float]
    is_anomaly: bool
    dominant_anomaly_type: Optional[str]
    total_logs: int
    anomaly_count: int


def compute_entropy(counts: list[int], total: int) -> float:
    """Shannon entropy of a count distribution."""
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in counts:
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return entropy


def extract_window_features(
    logs: list[ParsedLogRecord],
    window_id: str,
    window_duration_seconds: float,
) -> WindowFeatures:
    """Extract the 12-feature vector matching the production pipeline.

    Feature columns (matching backend/app/ml/anomaly_detector.FEATURE_COLUMNS):
        log_count, info_count, warning_count, error_count, error_ratio,
        active_services, unique_templates, dominant_service_count,
        dominant_template_count, logs_per_second, avg_logs_per_minute,
        burst_indicator
    """
    log_count = len(logs)

    # Level counts
    level_counter = Counter(log.level.lower() for log in logs)
    info_count = sum(1 for log in logs if log.level.lower() in {"info", "information", "notice"})
    warning_count = level_counter.get("warning", 0)
    error_count = level_counter.get("error", 0)
    error_ratio = error_count / log_count if log_count else 0.0

    # Service distribution
    service_counter = Counter(log.service for log in logs)
    active_services = len(service_counter)
    dominant_service = max(service_counter, key=lambda s: (service_counter[s], s), default=None)
    dominant_service_count = service_counter.get(dominant_service, 0) if dominant_service else 0

    # Template distribution
    template_counter = Counter(log.template_id for log in logs)
    unique_templates = len(template_counter)
    dominant_template = max(template_counter, key=lambda t: (template_counter[t], t), default=None)
    dominant_template_count = template_counter.get(dominant_template, 0) if dominant_template else 0

    # Temporal features
    logs_per_second = log_count / window_duration_seconds if window_duration_seconds > 0 else 0.0
    avg_logs_per_minute = logs_per_second * 60.0
    burst_indicator = 1.0 if logs_per_second >= 2.0 else 0.0

    features = {
        "log_count": float(log_count),
        "info_count": float(info_count),
        "warning_count": float(warning_count),
        "error_count": float(error_count),
        "error_ratio": float(error_ratio),
        "active_services": float(active_services),
        "unique_templates": float(unique_templates),
        "dominant_service_count": float(dominant_service_count),
        "dominant_template_count": float(dominant_template_count),
        "logs_per_second": float(logs_per_second),
        "avg_logs_per_minute": float(avg_logs_per_minute),
        "burst_indicator": float(burst_indicator),
    }
    feature_array = [features[col] for col in FEATURE_COLUMNS]

    # Ground-truth label: a window is anomalous if it contains any anomalous log
    anomaly_count = sum(1 for log in logs if log.is_anomaly)
    anomaly_types = [log.anomaly_type for log in logs if log.is_anomaly and log.anomaly_type]
    dominant_anomaly_type = Counter(anomaly_types).most_common(1)[0][0] if anomaly_types else None

    return WindowFeatures(
        window_id=window_id,
        features=features,
        feature_array=feature_array,
        is_anomaly=anomaly_count > 0,
        dominant_anomaly_type=dominant_anomaly_type,
        total_logs=log_count,
        anomaly_count=anomaly_count,
    )


def build_windows(
    parsed_logs: list[ParsedLogRecord],
    window_size_seconds: int = DEFAULT_WINDOW_SIZE_SECONDS,
    stride_seconds: int = DEFAULT_STRIDE_SECONDS,
) -> list[WindowFeatures]:
    """Segment parsed logs into overlapping time windows and extract features."""
    if not parsed_logs:
        return []

    sorted_logs = sorted(parsed_logs, key=lambda l: l.timestamp)
    earliest = sorted_logs[0].timestamp
    latest = sorted_logs[-1].timestamp

    windows: list[WindowFeatures] = []
    start = earliest
    window_idx = 0

    while start <= latest:
        end = start + timedelta(seconds=window_size_seconds)
        window_logs = [log for log in sorted_logs if start <= log.timestamp < end]

        if window_logs:
            wf = extract_window_features(
                window_logs,
                window_id=f"eval-window-{window_idx:04d}",
                window_duration_seconds=float(window_size_seconds),
            )
            windows.append(wf)
            window_idx += 1

        start += timedelta(seconds=stride_seconds)

    return windows


# ──────────────────────────────────────────────────────────────
# Model Training & Scoring
# ──────────────────────────────────────────────────────────────

def train_isolation_forest(
    normal_windows: list[WindowFeatures],
    contamination: float = DEFAULT_CONTAMINATION,
    seed: int = DEFAULT_SEED,
) -> IsolationForest:
    """Train an Isolation Forest on normal-only feature vectors."""
    X = np.array([w.feature_array for w in normal_windows], dtype=np.float64)
    model = IsolationForest(
        n_estimators=100,
        contamination=contamination,
        random_state=seed,
        n_jobs=-1,
    )
    model.fit(X)
    return model


def score_all_windows(
    model: IsolationForest,
    windows: list[WindowFeatures],
) -> tuple[np.ndarray, np.ndarray]:
    """Score all windows and return (raw_scores, predictions)."""
    X = np.array([w.feature_array for w in windows], dtype=np.float64)
    raw_scores = model.decision_function(X)
    predictions = model.predict(X)  # +1 = normal, -1 = anomaly
    return raw_scores, predictions


# ──────────────────────────────────────────────────────────────
# Classification Metrics
# ──────────────────────────────────────────────────────────────

def compute_confusion_matrix(
    ground_truth: list[bool],
    predicted: list[bool],
) -> ConfusionMatrix:
    """Compute TP/FP/TN/FN from boolean labels."""
    cm = ConfusionMatrix()
    for actual, pred in zip(ground_truth, predicted):
        if actual and pred:
            cm.tp += 1
        elif not actual and pred:
            cm.fp += 1
        elif not actual and not pred:
            cm.tn += 1
        else:
            cm.fn += 1
    return cm


def compute_roc_auc(
    ground_truth: list[bool],
    raw_scores: np.ndarray,
) -> float:
    """Compute ROC-AUC.  Lower raw_score → more anomalous, so we negate."""
    y_true = np.array([1 if gt else 0 for gt in ground_truth])
    if len(np.unique(y_true)) < 2:
        return 0.0
    # Negate scores: sklearn expects higher = more likely positive class
    return float(roc_auc_score(y_true, -raw_scores))


def apply_threshold(
    raw_scores: np.ndarray,
    threshold: float,
) -> list[bool]:
    """Apply a decision threshold: score < threshold → anomaly."""
    return [bool(score < threshold) for score in raw_scores]


def threshold_sweep(
    ground_truth: list[bool],
    raw_scores: np.ndarray,
    start: float = -0.80,
    stop: float = 0.01,
    step: float = 0.025,
) -> list[ThresholdResult]:
    """Sweep thresholds and compute metrics at each point."""
    results: list[ThresholdResult] = []
    roc_auc = compute_roc_auc(ground_truth, raw_scores)
    threshold = start
    while threshold < stop + 1e-9:
        predicted = apply_threshold(raw_scores, threshold)
        cm = compute_confusion_matrix(ground_truth, predicted)
        results.append(ThresholdResult(
            threshold=threshold,
            cm=cm,
            roc_auc=roc_auc,
        ))
        threshold = round(threshold + step, 6)
    return results


def find_optimal_threshold(
    sweep: list[ThresholdResult],
    max_fpr: float = 0.02,
    min_recall: float = 0.90,
) -> ThresholdResult:
    """Find the optimal threshold: minimize FPR (< 2%) while maintaining Recall (> 90%).

    Strategy:
    1. Filter candidates that satisfy both FPR < max_fpr AND Recall > min_recall.
    2. Among those, pick the one with the best F1 score.
    3. If no candidate meets both, relax: pick the threshold with the best F1
       that has Recall >= 0.5 (fallback).
    """
    # Primary: meet both constraints
    candidates = [
        r for r in sweep
        if r.cm.fpr <= max_fpr and r.cm.recall >= min_recall
    ]
    if candidates:
        return max(candidates, key=lambda r: r.cm.f1_score)

    # Fallback: best F1 with relaxed recall
    relaxed = [r for r in sweep if r.cm.recall >= 0.5]
    if relaxed:
        return max(relaxed, key=lambda r: r.cm.f1_score)

    # Last resort: best F1 overall
    return max(sweep, key=lambda r: r.cm.f1_score)


# ──────────────────────────────────────────────────────────────
# Per-Anomaly-Type Breakdown
# ──────────────────────────────────────────────────────────────

def per_type_analysis(
    windows: list[WindowFeatures],
    raw_scores: np.ndarray,
    threshold: float,
) -> dict[str, dict[str, Any]]:
    """Compute detection rates per anomaly type."""
    type_windows: dict[str, list[tuple[WindowFeatures, float]]] = {}
    for window, score in zip(windows, raw_scores):
        if window.is_anomaly and window.dominant_anomaly_type:
            atype = window.dominant_anomaly_type
            type_windows.setdefault(atype, []).append((window, score))

    results: dict[str, dict[str, Any]] = {}
    for atype, items in sorted(type_windows.items()):
        total = len(items)
        detected = sum(1 for _, score in items if score < threshold)
        missed = total - detected
        results[atype] = {
            "total_windows": total,
            "detected": detected,
            "missed": missed,
            "detection_rate": round(detected / total, 6) if total else 0.0,
        }
    return results


# ──────────────────────────────────────────────────────────────
# Markdown Reporting
# ──────────────────────────────────────────────────────────────

def print_markdown_report(result: EvaluationResult) -> None:
    """Print a clean Markdown summary to stdout."""
    dt = result.default_threshold
    ot = result.optimal_threshold

    print("\n## LogSentinel ML Accuracy Evaluation Report")
    print(f"\n**Run ID**: `{result.run_id}`")
    print(f"**Dataset**: {result.dataset['total_records']:,} logs "
          f"({result.dataset['normal_count']:,} normal / "
          f"{result.dataset['anomaly_count']:,} anomalous)")
    print(f"**Windows Generated**: {result.windows_generated}")
    print(f"**Drain3 Templates**: {result.drain3_stats['cluster_count']}")

    # ── Classification Metrics Table ─────────────────────────
    print("\n### Classification Metrics")
    print()
    print("| Metric | Default (score < 0) | Optimal (score < {:.4f}) |".format(ot.threshold))
    print("|:---|---:|---:|")
    print(f"| Precision | {dt.cm.precision:.4f} | {ot.cm.precision:.4f} |")
    print(f"| Recall (Sensitivity) | {dt.cm.recall:.4f} | {ot.cm.recall:.4f} |")
    print(f"| F1-Score | {dt.cm.f1_score:.4f} | {ot.cm.f1_score:.4f} |")
    print(f"| False Positive Rate | {dt.cm.fpr:.4f} | {ot.cm.fpr:.4f} |")
    print(f"| Accuracy | {dt.cm.accuracy:.4f} | {ot.cm.accuracy:.4f} |")
    print(f"| ROC-AUC | {dt.roc_auc:.4f} | {ot.roc_auc:.4f} |")

    # ── Confusion Matrix ─────────────────────────────────────
    print("\n### Confusion Matrix (Default Threshold)")
    print()
    print("|  | Predicted Normal | Predicted Anomaly |")
    print("|:---|---:|---:|")
    print(f"| **Actual Normal** | TN = {dt.cm.tn} | FP = {dt.cm.fp} |")
    print(f"| **Actual Anomaly** | FN = {dt.cm.fn} | TP = {dt.cm.tp} |")

    print("\n### Confusion Matrix (Optimal Threshold)")
    print()
    print("|  | Predicted Normal | Predicted Anomaly |")
    print("|:---|---:|---:|")
    print(f"| **Actual Normal** | TN = {ot.cm.tn} | FP = {ot.cm.fp} |")
    print(f"| **Actual Anomaly** | FN = {ot.cm.fn} | TP = {ot.cm.tp} |")

    # ── Per-Type Detection Rates ─────────────────────────────
    print("\n### Per-Anomaly-Type Detection Rate (Optimal Threshold)")
    print()
    print("| Anomaly Type | Windows | Detected | Missed | Detection Rate |")
    print("|:---|---:|---:|---:|---:|")
    for atype, data in result.per_anomaly_type.items():
        print(
            f"| {atype} | {data['total_windows']} | {data['detected']} | "
            f"{data['missed']} | {data['detection_rate']:.4f} |"
        )

    # ── Threshold Sweep Table ────────────────────────────────
    print("\n### Threshold Sweep Analysis")
    print()
    print("| Threshold | Precision | Recall | F1 | FPR | TP | FP | TN | FN |")
    print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for entry in result.threshold_sweep:
        print(
            f"| {entry['threshold']:.4f} "
            f"| {entry['precision']:.4f} "
            f"| {entry['recall']:.4f} "
            f"| {entry['f1_score']:.4f} "
            f"| {entry['fpr']:.4f} "
            f"| {entry['tp']} "
            f"| {entry['fp']} "
            f"| {entry['tn']} "
            f"| {entry['fn']} |"
        )

    # ── Timing ───────────────────────────────────────────────
    print("\n### Timing")
    print()
    for phase, seconds in result.timing.items():
        print(f"- **{phase}**: {seconds:.3f}s")

    # ── Verdict ──────────────────────────────────────────────
    print("\n### Verdict")
    meets_fpr = ot.cm.fpr < 0.02
    meets_recall = ot.cm.recall >= 0.90
    if meets_fpr and meets_recall:
        print(f"\n[PASS] Optimal threshold {ot.threshold:.4f} achieves "
              f"FPR={ot.cm.fpr:.4f} (< 2%) and Recall={ot.cm.recall:.4f} (>= 90%).")
    else:
        reasons: list[str] = []
        if not meets_fpr:
            reasons.append(f"FPR={ot.cm.fpr:.4f} (target < 2%)")
        if not meets_recall:
            reasons.append(f"Recall={ot.cm.recall:.4f} (target >= 90%)")
        print(f"\n[NEEDS TUNING] {'; '.join(reasons)}. "
              f"Consider adjusting contamination, feature engineering, "
              f"or expanding training data.")


# ──────────────────────────────────────────────────────────────
# CLI Entry Point
# ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate Isolation Forest classification accuracy for LogSentinel.",
    )
    parser.add_argument(
        "--total-records",
        type=int,
        default=DEFAULT_TOTAL_RECORDS,
        help=f"Total synthetic log entries to generate (default: {DEFAULT_TOTAL_RECORDS}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed for reproducibility (default: {DEFAULT_SEED}).",
    )
    parser.add_argument(
        "--contamination",
        type=float,
        default=DEFAULT_CONTAMINATION,
        help=f"IsolationForest contamination parameter (default: {DEFAULT_CONTAMINATION}).",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=DEFAULT_WINDOW_SIZE_SECONDS,
        help=f"Sliding window size in seconds (default: {DEFAULT_WINDOW_SIZE_SECONDS}).",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=DEFAULT_STRIDE_SECONDS,
        help=f"Stride between windows in seconds (default: {DEFAULT_STRIDE_SECONDS}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"JSON output path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--sweep-start",
        type=float,
        default=THRESHOLD_SWEEP_RANGE[0],
        help="Threshold sweep start (default: -0.80).",
    )
    parser.add_argument(
        "--sweep-stop",
        type=float,
        default=THRESHOLD_SWEEP_RANGE[1],
        help="Threshold sweep stop (default: 0.01).",
    )
    parser.add_argument(
        "--sweep-step",
        type=float,
        default=THRESHOLD_SWEEP_RANGE[2],
        help="Threshold sweep step (default: 0.025).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = f"ml-eval-{uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    print(f"LogSentinel ML Accuracy Evaluation -- {run_id}")
    print(f"Total records: {args.total_records:,} | Seed: {args.seed}")
    print(f"Normal/Anomaly split: {NORMAL_RATIO*100:.0f}% / {ANOMALY_RATIO*100:.0f}%")
    print(f"Window: {args.window_size}s / Stride: {args.stride}s")
    print(f"Contamination: {args.contamination}")
    print()

    timings: dict[str, float] = {}

    # ── Phase 1: Generate Dataset ────────────────────────────
    print("Phase 1: Generating labeled dataset ...", flush=True)
    t0 = time.perf_counter()
    dataset = generate_labeled_dataset(total=args.total_records, seed=args.seed)
    timings["dataset_generation"] = round(time.perf_counter() - t0, 3)

    normal_count = sum(1 for r in dataset if not r.is_anomaly)
    anomaly_count = sum(1 for r in dataset if r.is_anomaly)
    anomaly_types = Counter(r.anomaly_type for r in dataset if r.is_anomaly)
    print(f"  Generated {len(dataset):,} entries: "
          f"{normal_count:,} normal, {anomaly_count:,} anomalous")
    print(f"  Anomaly types: {dict(anomaly_types)}")

    # ── Phase 2: Drain3 Template Parsing ─────────────────────
    print("\nPhase 2: Parsing through Drain3 ...", flush=True)
    t0 = time.perf_counter()
    miner = make_template_miner()
    parsed_logs = parse_with_drain3(miner, dataset)
    timings["drain3_parsing"] = round(time.perf_counter() - t0, 3)

    clusters = list(miner.drain.clusters)
    drain3_stats = {
        "cluster_count": len(clusters),
        "total_cluster_size": miner.drain.get_total_cluster_size(),
    }
    print(f"  Templates discovered: {drain3_stats['cluster_count']}")
    print(f"  Total cluster size: {drain3_stats['total_cluster_size']}")

    # ── Phase 3: Window Feature Extraction ───────────────────
    print("\nPhase 3: Extracting window features ...", flush=True)
    t0 = time.perf_counter()
    all_windows = build_windows(
        parsed_logs,
        window_size_seconds=args.window_size,
        stride_seconds=args.stride,
    )
    timings["feature_extraction"] = round(time.perf_counter() - t0, 3)

    normal_windows = [w for w in all_windows if not w.is_anomaly]
    anomaly_windows = [w for w in all_windows if w.is_anomaly]
    print(f"  Windows generated: {len(all_windows)} "
          f"({len(normal_windows)} normal, {len(anomaly_windows)} anomalous)")

    if not normal_windows:
        print("ERROR: No normal windows generated. Cannot train model.", file=sys.stderr)
        return 1
    if not anomaly_windows:
        print("WARNING: No anomalous windows generated. Metrics will be trivial.",
              file=sys.stderr)

    # ── Phase 4: Train Isolation Forest ──────────────────────
    print("\nPhase 4: Training Isolation Forest ...", flush=True)
    t0 = time.perf_counter()
    model = train_isolation_forest(
        normal_windows,
        contamination=args.contamination,
        seed=args.seed,
    )
    timings["model_training"] = round(time.perf_counter() - t0, 3)
    print(f"  Model trained on {len(normal_windows)} normal windows "
          f"({model.n_estimators} estimators)")

    # ── Phase 5: Score All Windows ───────────────────────────
    print("\nPhase 5: Scoring all windows ...", flush=True)
    t0 = time.perf_counter()
    raw_scores, predictions = score_all_windows(model, all_windows)
    timings["scoring"] = round(time.perf_counter() - t0, 3)

    ground_truth = [w.is_anomaly for w in all_windows]

    # Default threshold: score < 0 → anomaly (sklearn convention)
    default_predicted = [bool(p == -1) for p in predictions]
    default_cm = compute_confusion_matrix(ground_truth, default_predicted)
    default_auc = compute_roc_auc(ground_truth, raw_scores)
    default_result = ThresholdResult(threshold=0.0, cm=default_cm, roc_auc=default_auc)

    # ── Phase 6: Threshold Sweep ─────────────────────────────
    print("\nPhase 6: Threshold sweep analysis ...", flush=True)
    t0 = time.perf_counter()
    sweep = threshold_sweep(
        ground_truth,
        raw_scores,
        start=args.sweep_start,
        stop=args.sweep_stop,
        step=args.sweep_step,
    )
    optimal = find_optimal_threshold(sweep)
    timings["threshold_sweep"] = round(time.perf_counter() - t0, 3)

    print(f"  Optimal threshold: {optimal.threshold:.4f} "
          f"(F1={optimal.cm.f1_score:.4f}, "
          f"FPR={optimal.cm.fpr:.4f}, "
          f"Recall={optimal.cm.recall:.4f})")

    # ── Phase 7: Per-Type Analysis ───────────────────────────
    per_type = per_type_analysis(all_windows, raw_scores, optimal.threshold)

    # ── Build Result ─────────────────────────────────────────
    finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    evaluation = EvaluationResult(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        dataset={
            "total_records": len(dataset),
            "normal_count": normal_count,
            "anomaly_count": anomaly_count,
            "normal_ratio": NORMAL_RATIO,
            "anomaly_ratio": ANOMALY_RATIO,
            "anomaly_types": dict(anomaly_types),
            "seed": args.seed,
        },
        drain3_stats=drain3_stats,
        windows_generated=len(all_windows),
        model_config={
            "n_estimators": model.n_estimators,
            "contamination": float(model.contamination),
            "random_state": model.random_state,
            "feature_columns": FEATURE_COLUMNS,
            "window_size_seconds": args.window_size,
            "stride_seconds": args.stride,
        },
        default_threshold=default_result,
        optimal_threshold=optimal,
        threshold_sweep=[r.to_dict() for r in sweep],
        per_anomaly_type=per_type,
        timing=timings,
    )

    # ── Export JSON ───────────────────────────────────────────
    def serialize(obj: Any) -> Any:
        if isinstance(obj, ThresholdResult):
            return obj.to_dict()
        if isinstance(obj, ConfusionMatrix):
            return obj.to_dict()
        if hasattr(obj, "__dict__"):
            return {k: serialize(v) for k, v in obj.__dict__.items()}
        if isinstance(obj, dict):
            return {k: serialize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [serialize(v) for v in obj]
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        return obj

    output_dict = serialize(evaluation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output_dict, indent=2), encoding="utf-8")

    # ── Print Markdown Report ────────────────────────────────
    print_markdown_report(evaluation)
    print(f"\nRaw evaluation data written to {args.output}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nEvaluation interrupted.", file=sys.stderr)
        raise SystemExit(130)

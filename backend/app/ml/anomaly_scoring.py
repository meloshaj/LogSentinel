"""Shared anomaly score normalization contract."""

from __future__ import annotations

import math
from typing import Any, Mapping


RAW_SCORE_DECAY = 4.0


def normalize_isolation_forest_score(raw_score: Any) -> float:
    """Normalize sklearn IsolationForest decision_function output to [0, 1].

    IsolationForest returns lower values for more anomalous observations and
    uses zero as the decision boundary.  Negative values are anomalous.  The
    normalized score is higher when the raw score is more anomalous.
    """
    raw = _safe_float(raw_score)
    if raw is None:
        return 0.0
    if raw < 0.0:
        return _clamp(1.0 - math.exp(raw * RAW_SCORE_DECAY))
    return 0.0


def normalize_prediction_anomaly_score(prediction: Mapping[str, Any]) -> float:
    """Return the normalized higher-is-more-anomalous score for a prediction."""
    is_anomaly = prediction.get("is_anomaly")
    if is_anomaly is False:
        return 0.0

    if "raw_score" in prediction:
        return normalize_isolation_forest_score(prediction.get("raw_score"))

    value = _safe_float(prediction.get("anomaly_score"))
    if value is None:
        return 0.0
    if 0.0 <= value <= 1.0:
        return _clamp(value)
    if value > 1.0:
        return _clamp(value / (1.0 + value))
    return normalize_isolation_forest_score(value)


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(converted):
        return None
    return converted


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))

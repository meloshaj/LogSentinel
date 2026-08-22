"""Explicitly convert the legacy Isolation Forest artifact to the active format.

The application never guesses between ``.pkl`` and ``.joblib``. Run this
operator-owned command when an existing legacy artifact should be adopted:

    python scripts/convert_model_artifact.py

The source is read, validated through the same detector loader used at runtime,
and written as a new canonical ``isolation_forest.joblib`` file. The source is
never renamed or deleted implicitly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.ml.anomaly_detector import (
    IsolationForestAnomalyDetector,
    get_canonical_model_path,
)

DEFAULT_SOURCE = REPOSITORY_ROOT / "backend" / "models" / "isolation_forest.pkl"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=get_canonical_model_path())
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing target artifact after it has been validated",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    target = args.target.resolve()
    if not source.is_file():
        parser.error(f"source artifact does not exist: {source}")
    if target.exists() and not args.force:
        parser.error(f"target already exists; pass --force to replace it: {target}")

    detector = IsolationForestAnomalyDetector.load_model(source)
    detector.save_model(target)
    print(f"Converted {source} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

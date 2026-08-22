import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.ml.anomaly_detector import IsolationForestAnomalyDetector
from backend.app.models import FeatureVector


class IsolationForestAnomalyDetectorTests(unittest.TestCase):
    def _build_feature_vector(self, *, log_count: int, error_count: int, unique_templates: int, window_id: str) -> FeatureVector:
        return FeatureVector(
            window_id=window_id,
            timestamp=datetime.now(timezone.utc),
            window_start=datetime.now(timezone.utc),
            window_end=datetime.now(timezone.utc) + timedelta(minutes=1),
            log_count=log_count,
            unique_templates=unique_templates,
            error_count=error_count,
            warning_count=0,
            template_frequencies={"template-1": 0.5},
            template_entropy=0.1,
            service_distribution={"auth": log_count},
            logs_per_second=float(log_count) / 60.0,
            feature_array=[float(log_count), float(error_count), float(unique_templates)],
            feature_names=["log_count", "error_count", "unique_templates"],
            features={
                "log_count": float(log_count),
                "info_count": float(log_count - error_count),
                "warning_count": 0.0,
                "error_count": float(error_count),
                "error_ratio": float(error_count / max(log_count, 1)),
                "active_services": 1.0,
                "unique_templates": float(unique_templates),
                "dominant_service_count": float(log_count),
                "dominant_template_count": float(unique_templates),
                "logs_per_second": float(log_count) / 60.0,
                "avg_logs_per_minute": float(log_count),
                "burst_indicator": 0.0,
                "dominant_service": "auth",
                "dominant_template": "template-1",
            },
        )

    def test_train_predict_and_save_load(self) -> None:
        normal_vectors = [
            self._build_feature_vector(log_count=20, error_count=1, unique_templates=3, window_id="normal-1"),
            self._build_feature_vector(log_count=18, error_count=2, unique_templates=2, window_id="normal-2"),
            self._build_feature_vector(log_count=22, error_count=1, unique_templates=3, window_id="normal-3"),
        ]
        anomaly_vectors = [
            self._build_feature_vector(log_count=200, error_count=80, unique_templates=6, window_id="anomaly-1"),
            self._build_feature_vector(log_count=220, error_count=90, unique_templates=7, window_id="anomaly-2"),
        ]

        detector = IsolationForestAnomalyDetector(random_state=42)
        detector.train(normal_vectors + anomaly_vectors)

        prediction = detector.predict(normal_vectors[0])
        self.assertIn("anomaly_score", prediction)
        self.assertIn("raw_score", prediction)
        self.assertIn("is_anomaly", prediction)
        self.assertIn("severity", prediction)
        self.assertIn("model_version", prediction)
        self.assertEqual(prediction["window_id"], "normal-1")
        self.assertGreaterEqual(prediction["anomaly_score"], 0.0)
        self.assertLessEqual(prediction["anomaly_score"], 1.0)
        if prediction["raw_score"] >= 0:
            self.assertEqual(prediction["anomaly_score"], 0.0)

        batch_predictions = detector.predict_batch(normal_vectors + anomaly_vectors)
        self.assertEqual(len(batch_predictions), len(normal_vectors + anomaly_vectors))
        self.assertTrue(any(item["is_anomaly"] for item in batch_predictions))
        self.assertTrue(all("raw_score" in item for item in batch_predictions))
        self.assertTrue(all(0.0 <= item["anomaly_score"] <= 1.0 for item in batch_predictions))

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "isolation_forest.joblib"
            detector.save_model(model_path)
            self.assertTrue(model_path.exists())

            loaded_detector = IsolationForestAnomalyDetector.load_model(model_path)
            loaded_prediction = loaded_detector.predict(normal_vectors[0])
            self.assertIn("anomaly_score", loaded_prediction)
            self.assertIn("raw_score", loaded_prediction)
            self.assertIn("is_anomaly", loaded_prediction)
            self.assertIn("severity", loaded_prediction)


if __name__ == "__main__":
    unittest.main()

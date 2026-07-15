import unittest
from datetime import datetime, timedelta, timezone

from backend.app.ml.feature_extractor import SlidingWindowFeatureExtractor, WindowConfig
from backend.app.models import ParsedLog


class SlidingWindowFeatureExtractorTests(unittest.TestCase):
    def test_add_log_and_extract_features(self) -> None:
        config = WindowConfig(window_size_seconds=60, stride_seconds=30)
        extractor = SlidingWindowFeatureExtractor(config)

        base_time = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
        logs = [
            ParsedLog(
                timestamp=base_time + timedelta(seconds=i),
                service="auth" if i % 2 == 0 else "db",
                level="error" if i % 3 == 0 else "info",
                raw_message=f"message {i}",
                template_id=f"template-{i % 2}",
                template_text="message <*>",
            )
            for i in range(6)
        ]

        for log in logs:
            extractor.add_log(log)

        current_window = extractor.get_current_window()
        self.assertIsNotNone(current_window)
        self.assertEqual(current_window.log_count(), 6)

        features = extractor.extract_features(current_window)
        self.assertEqual(features.log_count, 6)
        self.assertIn("log_count", features.feature_names)
        self.assertIn("error_count", features.feature_names)
        self.assertGreaterEqual(features.error_count, 0)

        closed_window = extractor.close_window(base_time + timedelta(minutes=1))
        self.assertIsNotNone(closed_window)
        self.assertEqual(closed_window.log_count, 6)
        self.assertIsNotNone(closed_window.feature_names)


if __name__ == "__main__":
    unittest.main()

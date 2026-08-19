import pytest
from datetime import datetime, timedelta, timezone

from backend.app.ml.feature_extractor import SlidingWindowFeatureExtractor, WindowConfig
from backend.app.models import ParsedLog


def test_add_log_and_extract_features(make_parsed_log) -> None:
    config = WindowConfig(window_size_seconds=60, stride_seconds=30)
    extractor = SlidingWindowFeatureExtractor(config)

    base_time = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
    logs = [
        make_parsed_log(
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

    pending_windows = extractor.get_pending_windows(base_time + timedelta(seconds=61))
    assert len(pending_windows) == 1
    current_window = pending_windows[0]
    assert current_window.log_count() == 6

    features = extractor.extract_features(current_window)
    assert features.log_count == 6
    assert "log_count" in features.feature_names
    assert "error_count" in features.feature_names
    assert features.error_count >= 0

def test_pending_windows_are_not_reemitted(make_parsed_log) -> None:
    config = WindowConfig(window_size_seconds=60, stride_seconds=30)
    extractor = SlidingWindowFeatureExtractor(config)

    base_time = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(6):
        extractor.add_log(
            make_parsed_log(
                timestamp=base_time + timedelta(seconds=i),
                service="auth",
                level="error",
                raw_message=f"message {i}",
                template_id="template-1",
                template_text="message <*>",
            )
        )

    first = extractor.get_pending_windows(base_time + timedelta(minutes=2))
    second = extractor.get_pending_windows(base_time + timedelta(minutes=2))

    assert len(first) == 1
    assert second == []

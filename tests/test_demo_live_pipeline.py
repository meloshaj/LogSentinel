from scripts.demo_live_pipeline import (
    build_ingest_batches,
    extract_feature_vectors,
    format_feature_summary,
    generate_synthetic_logs,
)


def test_generated_logs_contain_required_fields() -> None:
    logs = generate_synthetic_logs(total=12)

    assert logs
    for log in logs:
        assert set(log) >= {"service_name", "level", "message", "metadata"}
        assert isinstance(log["message"], str)
        assert log["metadata"]["correlation_id"]


def test_generated_logs_include_expected_services_and_levels() -> None:
    logs = generate_synthetic_logs(total=60)
    services = {log["service_name"] for log in logs}
    levels = {log["level"] for log in logs}

    assert {"auth-service", "payment-service", "order-service"} <= services
    assert {"INFO", "WARNING", "ERROR"} <= levels


def test_generated_batches_match_ingest_payload_shape() -> None:
    logs = generate_synthetic_logs(total=40)
    batches = build_ingest_batches(logs, batch_size=8)

    assert batches
    for batch in batches:
        assert set(batch) >= {"source", "environment", "correlation_id", "logs"}
        assert batch["source"] == "live-pipeline-demo"
        assert 5 <= len(batch["logs"]) <= 10
        for log in batch["logs"]:
            assert set(log) >= {"service_name", "level", "message", "metadata"}


def test_extract_feature_vectors_supports_confirmed_features_shape() -> None:
    feature = {
        "window_id": "window-1",
        "log_count": 64,
        "unique_templates": 9,
        "error_count": 15,
        "warning_count": 8,
        "service_distribution": {
            "auth-service": 22,
            "payment-service": 21,
            "order-service": 21,
        },
        "anomaly_prediction": None,
        "features": {
            "error_ratio": 0.234375,
            "unique_templates": 9.0,
        },
    }

    features = extract_feature_vectors({"features": [feature]})
    summary = format_feature_summary(features[0])

    assert features == [feature]
    assert "window_id=window-1" in summary
    assert "log_count=64" in summary
    assert "error_count=15" in summary
    assert "warning_count=8" in summary
    assert "error_ratio=0.234375" in summary
    assert "unique_templates=9" in summary
    assert "auth-service" in summary
    assert "payment-service" in summary
    assert "order-service" in summary
    assert "anomaly_prediction=none" in summary

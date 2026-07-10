from scripts.demo_live_pipeline import build_ingest_batches, generate_synthetic_logs


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

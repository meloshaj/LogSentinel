"""Static regression gates for the repository deployment contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def test_production_compose_has_one_explicit_worker_topology_and_migrator() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert 'RUN_EMBEDDED_WORKERS: "false"' in compose
    assert 'RUN_WEBHOOK_WORKER_IN_LIFESPAN: "false"' in compose
    assert 'RUN_ARCHIVE_WORKER_IN_LIFESPAN: "false"' in compose
    assert "db-migrator:" in compose and '"--ensure"' in compose
    assert '"run", "pipeline"' in compose
    assert '"run", "webhook"' in compose
    assert "nginx" not in compose.lower()


def test_production_compose_scopes_smtp_to_email_senders() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    smtp_names = (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "EMAILS_FROM_EMAIL",
    )
    assert "x-email-environment: &email-environment" in compose
    assert all(f"{name}: ${{{name}:?" in compose for name in smtp_names)

    backend_section = compose.split("  backend:", 1)[1].split("  pipeline-worker:", 1)[
        0
    ]
    pipeline_section = compose.split("  pipeline-worker:", 1)[1].split(
        "  webhook-worker:", 1
    )[0]
    webhook_section = compose.split("  webhook-worker:", 1)[1].split(
        "  archive-worker:", 1
    )[0]
    archive_section = compose.split("  archive-worker:", 1)[1].split("  caddy:", 1)[0]
    caddy_section = compose.split("  caddy:", 1)[1]

    assert "<<: [*backend-environment, *email-environment]" in backend_section
    assert "<<: [*backend-environment, *email-environment]" in webhook_section
    for section in (pipeline_section, archive_section, caddy_section):
        assert "email-environment" not in section
        assert not any(f"{name}:" in section for name in smtp_names)


def test_helm_has_single_pipeline_webhook_and_migration_owners() -> None:
    templates = ROOT / "deploy" / "helm" / "logsentinel" / "templates"
    assert (templates / "pipeline-worker-deployment.yaml").is_file()
    assert (templates / "webhook-worker-deployment.yaml").is_file()
    assert (templates / "migration-job.yaml").is_file()
    assert not any(
        (templates / name).exists()
        for name in (
            "drain-worker-deployment.yaml",
            "feature-worker-deployment.yaml",
            "event-worker-deployment.yaml",
        )
    )
    pipeline = (templates / "pipeline-worker-deployment.yaml").read_text(
        encoding="utf-8"
    )
    migration = (templates / "migration-job.yaml").read_text(encoding="utf-8")
    assert "type: Recreate" in pipeline
    assert "post-install,post-upgrade" in migration
    assert '"--ensure"' in migration


def test_caddy_and_metrics_contract_remain_private_and_restrictive() -> None:
    caddy = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert "path /metrics" in caddy and "respond @internal 403" in caddy
    assert "route {" in caddy
    assert caddy.index("respond @internal 403") < caddy.index(
        "handle {\n            root * /usr/share/caddy/html"
    )
    assert "encode zstd gzip" in caddy
    assert "script-src *" not in caddy
    assert "unsafe-eval" not in caddy
    prometheus = (ROOT / "deploy" / "monitoring" / "prometheus.yml").read_text(
        encoding="utf-8"
    )
    assert "credentials_file" in prometheus


def test_ci_actions_are_immutable_and_backend_is_scanned() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for line in ci.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40}(?:\s+#|\s*$)", line), line
    assert "pull_request_target" not in ci
    assert "contents: read" in ci
    assert "backend/Dockerfile" in ci
    assert "pip-audit" in ci and "npm audit --omit=dev" in ci


def test_monitoring_rules_match_the_configured_exporter_contract() -> None:
    alerts = (ROOT / "deploy" / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
    prometheus = (ROOT / "deploy" / "monitoring" / "prometheus.yml").read_text(
        encoding="utf-8"
    )
    assert "probe_success" not in alerts
    assert "node_filesystem_avail_bytes" in alerts
    assert "node-exporter:9100" in prometheus
    assert "logsentinel_outbox_items" in alerts
    assert "runbook_url" in alerts


def test_worker_heartbeat_alert_covers_all_production_worker_roles() -> None:
    alerts = (ROOT / "deploy" / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
    for role in ("pipeline", "webhook", "archive"):
        assert f'role="{role}"' in alerts


def test_recovery_scripts_are_explicit_and_fail_closed() -> None:
    backup = (ROOT / "scripts" / "backup_database.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts" / "restore_database.sh").read_text(encoding="utf-8")
    assert "S3_REGION" in backup and 'region_name=os.environ["S3_REGION"]' in backup
    assert "us-east-1" not in backup and "us-east-1" not in restore
    assert "--target-db" in restore and "--confirm-replace" in restore
    assert "|| true" not in restore

"""Contract tests for the normal PostgreSQL backup execution path."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.security.redaction import sanitize_exception_text


SCRIPT = Path(__file__).parents[1] / "scripts" / "backup_database.sh"


def _find_usable_bash() -> str | None:
    candidates = [shutil.which("bash")]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
        )
    for candidate in candidates:
        if candidate is None or not Path(candidate).exists():
            continue
        try:
            result = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, check=False
            )
        except OSError:
            continue
        if result.returncode == 0:
            return candidate
    return None


BASH = _find_usable_bash()
pytestmark = pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="backup contract shell tests run on Linux CI",
)


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def backup_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "pg_dump",
        """#!/bin/sh
if [ "$1" = "--version" ]; then
  printf 'pg_dump (PostgreSQL) %s\\n' "${FAKE_CLIENT_VERSION:-16.6}"
  exit 0
fi
if [ "${FAKE_PG_DUMP_FAIL:-0}" = "1" ]; then
  for arg in "$@"; do
    if [ "$arg" = "--file" ]; then
      shift
      printf 'partial dump' > "$1"
      break
    fi
    shift
  done
  exit 1
fi
output=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--file" ]; then output="$2"; shift 2; else shift; fi
done
printf 'valid custom-format dump for contract test\\n' > "$output"
""",
    )
    _executable(
        fake_bin / "psql",
        """#!/bin/sh
case "$*" in
  *'SHOW server_version'*) printf '%s\\n' "${FAKE_SERVER_VERSION:-16.6}" ;;
  *timescaledb*) printf '%s\\n' '2.17.2' ;;
  *schema_migrations*) printf '%s\\n' '20260913_0010_per_user_data_ownership' ;;
  *) printf '%s\\n' 'ok' ;;
esac
""",
    )
    _executable(
        fake_bin / "pg_restore",
        """#!/bin/sh
if [ "${FAKE_PG_RESTORE_FAIL:-0}" = "1" ]; then exit 1; fi
printf '1; ; DATABASE ; logsentinel_db ; DATABASE\\n'
""",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}",
            "POSTGRES_HOST": "timescaledb",
            "POSTGRES_USER": "logsentinel",
            "POSTGRES_DB": "logsentinel_db",
            "POSTGRES_PASSWORD": "test-only-not-production",
            "BACKUP_DIR": str(tmp_path / "backups"),
            "REQUIRE_REMOTE_BACKUP": "false",
            "BACKUP_CLEANUP_ENABLED": "false",
            "FAKE_SERVER_VERSION": "16.6",
            "FAKE_CLIENT_VERSION": "16.6",
        }
    )
    return environment


def _run(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH or "bash", str(SCRIPT)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _artifacts(environment: dict[str, str]) -> tuple[Path, Path, Path]:
    backup_dir = Path(environment["BACKUP_DIR"])
    dump = next(backup_dir.glob("*.dump"))
    return dump, Path(f"{dump}.sha256"), Path(f"{dump}.manifest.json")


def test_pg16_server_and_pg16_client_pass_and_create_contract_artifacts(
    backup_environment: dict[str, str],
) -> None:
    result = _run(backup_environment)

    assert result.returncode == 0, result.stderr
    dump, checksum, manifest = _artifacts(backup_environment)
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    assert dump.stat().st_size > 0
    assert checksum.read_text(encoding="utf-8").startswith(f"{digest}  ")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    assert metadata["database_logical_identifier"] == "logsentinel_db"
    assert metadata["server_postgresql_major"] == 16
    assert metadata["pg_dump_major"] == 16
    assert metadata["timescaledb_version"] == "2.17.2"
    assert metadata["migration_head"].endswith("0010_per_user_data_ownership")
    assert metadata["dump_filename"] == dump.name
    assert metadata["dump_sha256"] == digest
    assert metadata["backup_execution_source"] == "scripts/backup_database.sh"
    assert metadata["backup_execution_version"] == "logsentinel-backup-v2"


def test_pg16_server_and_pg15_client_fail_closed(
    backup_environment: dict[str, str],
) -> None:
    backup_environment["FAKE_CLIENT_VERSION"] = "15.19"
    result = _run(backup_environment)

    assert result.returncode != 0
    assert "PostgreSQL backup client major version mismatch" in result.stderr
    assert "test-only-not-production" not in result.stderr
    assert not list(Path(backup_environment["BACKUP_DIR"]).glob("*.dump"))


def test_missing_pg_dump_fails_closed(backup_environment: dict[str, str]) -> None:
    fake_bin = Path(backup_environment["PATH"].split(os.pathsep)[0])
    (fake_bin / "pg_dump").unlink()
    backup_environment["PATH"] = str(fake_bin)
    result = _run(backup_environment)

    assert result.returncode != 0
    assert "PostgreSQL backup client pg_dump is required" in result.stderr


def test_backup_command_failure_fails_closed_and_removes_partial_dump(
    backup_environment: dict[str, str],
) -> None:
    backup_environment["FAKE_PG_DUMP_FAIL"] = "1"
    result = _run(backup_environment)

    assert result.returncode != 0
    backup_dir = Path(backup_environment["BACKUP_DIR"])
    assert "PostgreSQL backup command failed" in result.stderr
    assert not list(backup_dir.glob("*.dump"))
    assert not list(backup_dir.glob("*.partial"))


def test_corrupt_dump_fails_structural_validation(
    backup_environment: dict[str, str],
) -> None:
    backup_environment["FAKE_PG_RESTORE_FAIL"] = "1"
    result = _run(backup_environment)

    assert result.returncode != 0
    backup_dir = Path(backup_environment["BACKUP_DIR"])
    assert "PostgreSQL backup failed structural validation" in result.stderr
    assert not list(backup_dir.glob("*.dump"))
    assert not list(backup_dir.glob("*.partial"))


def test_manifest_contains_no_credentials(backup_environment: dict[str, str]) -> None:
    backup_environment["S3_ACCESS_KEY_ID"] = "test-access-marker"
    backup_environment["S3_SECRET_ACCESS_KEY"] = "test-secret-marker"
    result = _run(backup_environment)

    assert result.returncode == 0, result.stderr
    _, _, manifest = _artifacts(backup_environment)
    contents = manifest.read_text(encoding="utf-8")
    assert "test-access-marker" not in contents
    assert "test-secret-marker" not in contents
    assert "POSTGRES_PASSWORD" not in contents
    assert "S3_SECRET_ACCESS_KEY" not in contents


def test_remote_upload_requires_and_verifies_each_object(
    backup_environment: dict[str, str],
    tmp_path: Path,
) -> None:
    module_dir = tmp_path / "fake-python"
    module_dir.mkdir()
    evidence = tmp_path / "remote-evidence.json"
    (module_dir / "boto3.py").write_text(
        """import hashlib
import json
import os
from pathlib import Path

class Client:
    def __init__(self):
        self.objects = {}

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.objects[key] = (bucket, Path(filename).stat().st_size, ExtraArgs['Metadata']['sha256'])

    def head_object(self, Bucket, Key):
        bucket, length, digest = self.objects[Key]
        Path(os.environ['FAKE_REMOTE_EVIDENCE']).write_text(
            json.dumps({'bucket': bucket, 'key': Key, 'length': length, 'sha256': digest}),
            encoding='utf-8',
        )
        return {'ContentLength': length, 'Metadata': {'sha256': digest}}

def client(*args, **kwargs):
    return Client()
""",
        encoding="utf-8",
    )
    backup_environment.update(
        {
            "PYTHONPATH": f"{module_dir}{os.pathsep}{Path.cwd()}",
            "S3_BACKUP_BUCKET": "approved-backup-bucket",
            "S3_BUCKET_NAME": "archive-bucket-must-not-win",
            "S3_REGION": "us-east-1",
            "REQUIRE_REMOTE_BACKUP": "true",
            "S3_ACCESS_KEY_ID": "test-access-marker",
            "S3_SECRET_ACCESS_KEY": "test-secret-marker",
            "FAKE_REMOTE_EVIDENCE": str(evidence),
        }
    )
    result = _run(backup_environment)

    assert result.returncode == 0, result.stderr
    assert "Remote upload and integrity verification passed" in result.stdout
    remote = json.loads(evidence.read_text(encoding="utf-8"))
    assert remote["bucket"] == "approved-backup-bucket"
    assert remote["key"].startswith("backups/logsentinel_logsentinel_db_")
    assert remote["length"] > 0
    assert len(remote["sha256"]) == 64


def test_remote_upload_failure_fails_closed_without_secret_output(
    backup_environment: dict[str, str],
    tmp_path: Path,
) -> None:
    module_dir = tmp_path / "fake-python"
    module_dir.mkdir()
    (module_dir / "boto3.py").write_text(
        """class Client:
    def upload_file(self, *args, **kwargs):
        raise RuntimeError('remote failure with credentials must not escape')

def client(*args, **kwargs):
    return Client()
""",
        encoding="utf-8",
    )
    backup_environment.update(
        {
            "PYTHONPATH": f"{module_dir}{os.pathsep}{Path.cwd()}",
            "S3_BUCKET": "approved-backup-bucket",
            "S3_REGION": "us-east-1",
            "REQUIRE_REMOTE_BACKUP": "true",
            "S3_ACCESS_KEY_ID": "test-access-marker",
            "S3_SECRET_ACCESS_KEY": "test-secret-marker",
        }
    )
    result = _run(backup_environment)

    assert result.returncode != 0
    assert "Remote backup upload or verification failed" in result.stderr
    assert "credentials must not escape" not in result.stderr
    assert "test-secret-marker" not in result.stderr


def test_remote_provider_exception_uses_canonical_sanitization_contract(
    backup_environment: dict[str, str],
    tmp_path: Path,
) -> None:
    """Provider diagnostics retain safe context without leaking secret-shaped text."""
    module_dir = tmp_path / "fake-python"
    module_dir.mkdir()
    (module_dir / "boto3.py").write_text(
        """class Client:
    def upload_file(self, *args, **kwargs):
        raise RuntimeError(
            'postgresql://user:SENTINEL_DB_PASSWORD@host/db '
            'redis://:SENTINEL_REDIS_SECRET@host '
            'https://SENTINEL_USER:SENTINEL_PASSWORD@example.invalid/path '
            'AWS_ACCESS_KEY_ID=SENTINEL_ACCESS_KEY '
            'AWS_SECRET_ACCESS_KEY=SENTINEL_SECRET_KEY '
            'AWS_SESSION_TOKEN=SENTINEL_SESSION_TOKEN '
            'token=SENTINEL_TOKEN code=SENTINEL_CODE state=SENTINEL_STATE '
            'Authorization: Bearer SENTINEL_BEARER\\r\\n' + 'x' * 10000
        )

def client(*args, **kwargs):
    return Client()
""",
        encoding="utf-8",
    )
    backup_environment.update(
        {
            "PYTHONPATH": f"{module_dir}{os.pathsep}{Path.cwd()}",
            "S3_BUCKET": "approved-backup-bucket",
            "S3_REGION": "us-east-1",
            "REQUIRE_REMOTE_BACKUP": "true",
            "S3_ACCESS_KEY_ID": "SENTINEL_ACCESS_KEY",
            "S3_SECRET_ACCESS_KEY": "SENTINEL_SECRET_KEY",
        }
    )

    result = _run(backup_environment)

    assert result.returncode != 0
    assert "Remote backup upload or verification failed" in result.stderr
    assert "RuntimeError" in result.stderr
    assert "\r" not in result.stderr
    assert "\n" not in result.stderr.rstrip("\n")
    assert len(result.stderr) < 3000
    for sentinel in (
        "SENTINEL_DB_PASSWORD",
        "SENTINEL_REDIS_SECRET",
        "SENTINEL_USER",
        "SENTINEL_PASSWORD",
        "SENTINEL_ACCESS_KEY",
        "SENTINEL_SECRET_KEY",
        "SENTINEL_SESSION_TOKEN",
        "SENTINEL_TOKEN",
        "SENTINEL_CODE",
        "SENTINEL_STATE",
        "SENTINEL_BEARER",
    ):
        assert sentinel not in result.stderr


def test_backup_exception_helper_sanitizes_provider_payloads_on_all_platforms() -> None:
    """The exact helper imported by backup_database.sh is deterministic on Windows too."""
    exc = RuntimeError(
        "postgresql://user:SENTINEL_DB_PASSWORD@host/db "
        "redis://:SENTINEL_REDIS_SECRET@host "
        "AWS_SECRET_ACCESS_KEY=SENTINEL_SECRET_KEY "
        "Authorization: Bearer SENTINEL_BEARER\r\n" + "x" * 10000
    )

    safe = sanitize_exception_text(exc, maximum_length=1024)

    assert safe.startswith("RuntimeError:")
    assert "SENTINEL_DB_PASSWORD" not in safe
    assert "SENTINEL_REDIS_SECRET" not in safe
    assert "SENTINEL_SECRET_KEY" not in safe
    assert "SENTINEL_BEARER" not in safe
    assert "\r" not in safe
    assert "\n" not in safe
    assert len(safe) <= 1024

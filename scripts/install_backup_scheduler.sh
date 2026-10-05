#!/usr/bin/env bash
set -Eeuo pipefail

unit_source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/systemd" && pwd)"
mode="${1:---check}"

case "$mode" in
  --check)
    if command -v systemd-analyze >/dev/null 2>&1; then
      systemd-analyze verify \
        "$unit_source_dir/logsentinel-backup.service" \
        "$unit_source_dir/logsentinel-backup.timer"
    else
      grep -Eq '^Environment=COMPOSE_PROFILES=backup$' \
        "$unit_source_dir/logsentinel-backup.service"
      grep -Eq '^ExecStart=/usr/bin/docker compose .*run --rm --no-deps backup$' \
        "$unit_source_dir/logsentinel-backup.service"
      grep -Eq '^OnCalendar=\*-\*-\* 02:00:00 UTC$' \
        "$unit_source_dir/logsentinel-backup.timer"
      grep -Eq '^RandomizedDelaySec=15min$' \
        "$unit_source_dir/logsentinel-backup.timer"
      grep -Eq '^Persistent=true$' \
        "$unit_source_dir/logsentinel-backup.timer"
    fi
    echo "Backup scheduler unit validation passed"
    ;;
  --install)
    if [[ "${LOGSENTINEL_BACKUP_SCHEDULE_APPROVED:-false}" != "true" ]]; then
      echo "Operator approval is required before enabling the backup schedule" >&2
      exit 3
    fi
    install -m 0644 "$unit_source_dir/logsentinel-backup.service" \
      /etc/systemd/system/logsentinel-backup.service
    install -m 0644 "$unit_source_dir/logsentinel-backup.timer" \
      /etc/systemd/system/logsentinel-backup.timer
    systemctl daemon-reload
    systemctl enable --now logsentinel-backup.timer
    systemctl status --no-pager logsentinel-backup.timer
    systemctl list-timers --all logsentinel-backup.timer
    ;;
  *)
    echo "Usage: $0 [--check|--install]" >&2
    exit 2
    ;;
esac

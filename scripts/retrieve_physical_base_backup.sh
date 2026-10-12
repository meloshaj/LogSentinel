#!/usr/bin/env sh
set -eu

exec python3 /repo/scripts/retrieve_physical_base_backup.py "$@"

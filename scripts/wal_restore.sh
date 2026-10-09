#!/usr/bin/env sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "WAL restore requires PostgreSQL filename and destination placeholders" >&2
  exit 2
fi

exec python3 /usr/local/lib/logsentinel/wal_restore.py "$1" "$2"

#!/usr/bin/env sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "WAL archive requires PostgreSQL source and filename placeholders" >&2
  exit 2
fi

exec python3 /usr/local/lib/logsentinel/wal_archive.py "$1" "$2"

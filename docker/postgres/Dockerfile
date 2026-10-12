# PostgreSQL/TimescaleDB image with the smallest repository-owned WAL archive
# and restore helpers.  Recovery storage credentials are supplied only through
# the deployment environment; no credential is copied into this image.
# timescale/timescaledb:2.17.2-pg16; immutable multi-arch OCI index.
FROM timescale/timescaledb@sha256:4e459e217f00cbb09920c34d245501e63427e6767a495de57ce76823ff280f12

USER root

RUN apk add --no-cache ca-certificates py3-boto3

RUN install -d -m 0755 /usr/local/lib/logsentinel

COPY scripts/pitr_storage.py \
     scripts/wal_archive.py \
     scripts/wal_restore.py \
     scripts/wal_archive_status.py \
     /usr/local/lib/logsentinel/
COPY scripts/wal_archive.sh /usr/local/bin/logsentinel-wal-archive.sh
COPY scripts/wal_restore.sh /usr/local/bin/logsentinel-wal-restore.sh

RUN chmod 0755 /usr/local/bin/logsentinel-wal-archive.sh \
               /usr/local/bin/logsentinel-wal-restore.sh \
    && python3 -m py_compile /usr/local/lib/logsentinel/pitr_storage.py \
                           /usr/local/lib/logsentinel/wal_archive.py \
                           /usr/local/lib/logsentinel/wal_restore.py \
                           /usr/local/lib/logsentinel/wal_archive_status.py \
    && rm -rf /root/.cache

USER postgres

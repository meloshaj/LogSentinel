#!/bin/sh
# --------------------------------------------------------------------------
# LogSentinel NGINX Entrypoint — TLS Certificate Auto-Provisioning
# --------------------------------------------------------------------------
# If no TLS certificates are mounted at /etc/nginx/ssl/, generate a
# self-signed certificate pair so the container can start without manual
# certificate provisioning.  In production, mount real certificates via
# Docker secrets or a volume.
# --------------------------------------------------------------------------

set -e

CERT_DIR="/etc/nginx/ssl"
CERT_FILE="$CERT_DIR/server.crt"
KEY_FILE="$CERT_DIR/server.key"

mkdir -p "$CERT_DIR"

if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "[LogSentinel] No TLS certificates found at $CERT_DIR."
    echo "[LogSentinel] Generating self-signed TLS certificates for development/staging..."
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$KEY_FILE" \
        -out "$CERT_FILE" \
        -subj "/CN=localhost/O=LogSentinel/OU=Development" \
        2>/dev/null
    echo "[LogSentinel] Self-signed certificates generated successfully."
else
    echo "[LogSentinel] TLS certificates found — using mounted certificates."
fi

exec nginx -g "daemon off;"

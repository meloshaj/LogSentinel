#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SSL_DIR="$DIR/ssl"
mkdir -p "$SSL_DIR"

if [ -f "$SSL_DIR/server.crt" ]; then
    echo "Certificates already exist in $SSL_DIR"
    exit 0
fi

echo "Generating self-signed ECDSA certificate for local development..."
openssl ecparam -genkey -name prime256v1 -out "$SSL_DIR/server.key"
openssl req -new -x509 -sha256 -nodes -days 365 -key "$SSL_DIR/server.key" -out "$SSL_DIR/server.crt" -subj "/C=US/ST=State/L=City/O=LogSentinel/CN=localhost"

echo "Certificates generated in $SSL_DIR"

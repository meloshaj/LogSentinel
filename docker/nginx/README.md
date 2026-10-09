# Legacy Nginx Reference — Unsupported

This directory is retained only as historical reference. It is not part of any
supported Compose, Helm, CI, or production deployment path and its routing,
TLS, CORS, and security-header behavior is not maintained.

The canonical single-host edge is `deploy/caddy/Caddyfile`. Kubernetes uses
the operator-selected Ingress implementation described by the Helm chart.

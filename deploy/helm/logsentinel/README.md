# LogSentinel Helm chart

This chart deploys the API and background workers only. The React dashboard is
built and served by the root `Dockerfile`/Caddy deployment; it is intentionally
not duplicated in this chart. The ingress paths are restricted to backend API,
ingestion, OTLP, and WebSocket routes. Provide the frontend origin through
`FRONTEND_URL` and route it to the separately deployed Caddy service.

Production installations must pre-create the `secret.existingSecret` (the
default is `logsentinel-secrets`), pin `image.tag` to an immutable release (or set
`image.digest` in the deployment overlay), and allow the post-install/
post-upgrade migration Job to complete. The Job uses `--ensure`: it bootstraps
an empty database from `scripts/init.sql`, or applies the allowlisted forward
migrations to a known canonical schema. Unknown/non-empty schemas fail closed.
Set `env.postgresHost` and `env.redisHost` explicitly; this chart does not
silently deploy or assume same-release database/Valkey Services.

The supported production worker topology is one `pipeline` Deployment (Drain,
feature, event, and stream-cleaner responsibilities in one process) plus one
or more `webhook` Deployments consuming `pipeline_outbox`. The pipeline is
restricted to one replica while its feature windows remain process-local.

The default NetworkPolicy is disabled because external database/Valkey and SaaS
egress are deployment-specific. If enabled, configure selectors/CIDRs and
remember that standard Kubernetes NetworkPolicy cannot filter arbitrary SaaS
hostnames; strict hostname policy requires an external egress control.

The optional ServiceMonitor scrapes the internal metrics Service with the
`METRICS_TOKEN` from the configured Secret. It is never exposed by the public
Ingress.

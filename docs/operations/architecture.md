# Authoritative runtime architecture

```mermaid
flowchart TD
  Browser --> Edge[Caddy or Ingress]
  Edge --> API[API / WebSocket]
  API --> DB[(PostgreSQL / TimescaleDB)]
  API --> V[Valkey]
  V --> P[Pipeline worker - Drain + Feature + Event]
  P --> DB
  P --> O[(pipeline_outbox)]
  O --> W[Webhook outbox worker]
  W --> SaaS[Configured tenant webhook destinations]
  P --> A[Optional archive worker]
  A --> S3[(S3-compatible storage)]
  M[Migration owner] --> DB
```

There is one migration owner per deployment. The API and workers require the
latest recorded migration before serving work. Pipeline handoffs are currently
process-local, so production has one pipeline replica and uses `Recreate`
rollout semantics. Webhook delivery is durable and lease-reclaimable through
`pipeline_outbox`; it does not depend on an in-memory task.

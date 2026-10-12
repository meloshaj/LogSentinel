# Deployment matrix

| Mode | Edge | Worker topology | Database | Valkey | Migration owner | Backup model | Intended use |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Local Compose | Vite dev server | Embedded pipeline and webhook | Developer-provided | Developer-provided | Manual lifecycle command | None | Development |
| Demo Compose | Caddy/dev edge | Embedded pipeline; webhook in API lifespan | Local Timescale | Local Valkey | Compose init + explicit lifecycle | None | Demonstration |
| Production-like Compose | Caddy on 8080/8443 internally | API + one pipeline + one webhook; archive profile optional | Single-host Timescale volume | Single-host Valkey volume | `db-migrator` (`--ensure`) | Optional logical S3 backup | Single-host production-like, not HA |
| Kubernetes Helm | Ingress/load balancer | API + one pipeline + webhook; archive optional | External managed/self-managed Timescale | External managed/self-managed Valkey | One Helm migration Job (`--ensure`) | Optional backup CronJob or managed PITR | Distributed production deployment |

Kubernetes and production-like Compose set all embedded-worker flags to false.
The API never performs migrations. The same immutable backend image is used for
the migration owner and all Python workloads. Docker, Helm, cloud networking,
OAuth, restore, and capacity remain externally validated until executed there.

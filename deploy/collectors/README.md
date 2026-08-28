# LogSentinel Collectors

This directory contains turnkey, production-ready configurations for deploying modern, high-performance telemetry agents (Fluent Bit and Vector) to stream logs into LogSentinel.

## 1. Fluent Bit

Fluent Bit is configured to tail Kubernetes container logs and host syslog, extracting relevant Kubernetes metadata and mapping it to the canonical LogSentinel ingestion format.

### Test Locally via Docker Compose

```bash
cd fluent-bit
export LOGSENTINEL_HOST=localhost
export LOGSENTINEL_PORT=8000
export LOGSENTINEL_API_KEY=change_me

docker-compose -f docker-compose.collector.yml up -d
```

## 2. Vector

Vector is configured using VRL (Vector Remap Language) to consume the local Docker daemon socket natively. It normalizes all incoming logs—even unpacking nested JSON strings when present—into LogSentinel's canonical JSON schema.

### Test Locally via Docker CLI

```bash
cd vector
export LOGSENTINEL_HOST=host.docker.internal 
export LOGSENTINEL_API_KEY=change_me

docker run -d --name logsentinel-vector \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v $(pwd)/vector.yaml:/etc/vector/vector.yaml:ro \
  -e LOGSENTINEL_HOST=$LOGSENTINEL_HOST \
  -e LOGSENTINEL_API_KEY=$LOGSENTINEL_API_KEY \
  timberio/vector:0.38.0-alpine
```

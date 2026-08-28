# Day 7: Production Cutover & Rollout Runbook

This runbook serves as the final operational guide for the LogSentinel production launch. It details the step-by-step cutover sequence, the Go/No-Go readiness checklist, and the rollback procedure.

---

## 🚦 Step 1: The Go / No-Go Readiness Review

Before executing the production deployment, the lead operator must verify all previous Day 1-6 prerequisites are complete:

- [ ] **Day 1**: Kubernetes resource limits, DB connection pools, and batch tuning parameters are configured in `values.yaml`.
- [ ] **Day 2**: Failover logic is verified.
- [ ] **Day 3**: Secrets are externalized, the restricted `logsentinel_app` DB role exists, and `NetworkPolicies` isolate the workers.
- [ ] **Day 4**: Grafana dashboards are deployed and Slack/PagerDuty webhooks are wired.
- [ ] **Day 5**: TimescaleDB WAL archiving (pgBackRest) is actively backing up the production DB.
- [ ] **Day 6**: The React Frontend builds successfully with zero CSP errors.
- [ ] **CI/CD**: The GitHub Actions pipeline reports **zero CRITICAL vulnerabilities** via Trivy.

---

## 🏗️ Step 2: Staging Smoke Test & Dry-Run

Perform a Helm dry-run against the production namespace to validate syntax and template compilation without making actual cluster changes.

```bash
# Set your context to the production cluster
kubectl config use-context logsentinel-prod

# Execute a Helm dry-run
helm upgrade --install logsentinel deploy/helm/logsentinel \
  --namespace logsentinel-prod \
  --create-namespace \
  --dry-run \
  --debug
```
*Look for any rendered YAML errors or missing Secret references in the output.*

---

## 🚀 Step 3: The Production Cutover Sequence

Execute the deployment in a strict, dependency-ordered sequence to prevent race conditions.

### Phase A: State & Storage (Database & Stream)
1. Deploy the production TimescaleDB cluster and Valkey/Redis StatefulSet (if not using managed cloud services like AWS RDS/ElastiCache).
2. Execute the schema initialization:
   ```bash
   psql -h $PROD_DB_HOST -U logsentinel -d logsentinel_db -f scripts/init.sql
   ```

### Phase B: Background Workers (Consumers)
Deploy the heavy data processors first. They will connect to Valkey, build the consumer groups, and wait idly for data.
```bash
helm upgrade --install logsentinel deploy/helm/logsentinel \
  --namespace logsentinel-prod \
  --set replicaCount.api=0 \
  --set replicaCount.drainWorker=2 \
  --set replicaCount.eventWorker=1
```
*Wait for pods to report `Running` and `Ready` via `kubectl get pods -n logsentinel-prod`.*

### Phase C: API Gateway & Ingress (Producers)
Scale up the ingestion layer to open the floodgates.
```bash
helm upgrade --install logsentinel deploy/helm/logsentinel \
  --namespace logsentinel-prod \
  --set replicaCount.api=2
```

### Phase D: Edge Cutover (DNS & Log Forwarders)
1. Update DNS records (e.g., Route53, Cloudflare) for `logsentinel.local` to point to your Production Ingress Controller IP.
2. Update the configurations of your edge collectors (Fluent Bit, OpenTelemetry Collector, Vector) to point to the new `https://logsentinel.local/v1/logs` endpoint.

---

## 🩺 Step 4: Post-Deployment Monitoring (The First 2 Hours)

Immediately open Grafana and monitor the following for 2 hours post-cutover:
1. **HTTP 5xx Errors**: Ensure the API gateway maintains > 99.9% availability.
2. **Valkey Queue Depth**: The `pending` message count in `logs:stream` must remain near zero (indicating workers are keeping up with the ingestion rate).
3. **Database CAGG Job**: Ensure the `logs_rollup_1m` continuous aggregate runs successfully within its first scheduled interval.

---

## ⏪ Step 5: Emergency Rollback Procedure

If severe data corruption, memory leaks, or unrecoverable crashes occur, execute the rollback plan immediately:

1. **Revert Edge Traffic**: Reconfigure FluentBit / OTel to pause forwarding or route back to the legacy logging system.
2. **Helm Rollback**:
   ```bash
   # Find the previous stable revision
   helm history logsentinel -n logsentinel-prod
   
   # Rollback to revision 1 (or your last known good state)
   helm rollback logsentinel 1 -n logsentinel-prod
   ```
3. **Purge Corrupted Queues**: If the Valkey stream contains poison pills that bypassed the DLQ, truncate the stream:
   ```bash
   kubectl exec -it logsentinel-redis-0 -- redis-cli XTRIM logs:stream MAXLEN 0
   ```

🎉 **Congratulations on a successful deployment of LogSentinel!** 🎉

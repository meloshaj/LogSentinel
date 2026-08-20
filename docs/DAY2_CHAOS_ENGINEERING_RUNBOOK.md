# Day 2: Chaos Engineering & Failover Verification Runbook

This runbook outlines the methodology, validation steps, and automated fault injection tests for **Day 2: Chaos Engineering**.

---

## 🌪️ Overview

The LogSentinel architecture relies heavily on asynchronous stream processing (Valkey) and bulk database persistence (TimescaleDB). This runbook validates system resilience against catastrophic component failures, ensuring **zero data loss** and **graceful auto-recovery**.

---

## 🛠️ Step 1: Automated Fault Injection Suite

Run our pre-built Pytest fault-injection suites to programmatically validate the resilience logic:

```bash
# 1. Valkey/Redis Stream Consumer Recovery & PEL (Pending Entries List) Auto-Claim
pytest tests/test_redis_stream_resilience.py -v

# 2. Poison-Pill Isolation & Dead-Letter Queue (DLQ) Routing
pytest tests/test_dlq.py -v

# 3. Comprehensive Database Fault Injection (Simulated Partitions & Timeout Retries)
pytest tests/test_resilience_fault_injection.py -v
```

### Key Automated Validations:
* `test_pel_auto_recovery_and_processing`: Simulates a worker crash during `XREADGROUP` (without `XACK`) and verifies a new worker correctly claims and processes the pending messages.
* `test_poison_pill_routing`: Ensures corrupted logs failing Drain3 parsing 3 consecutive times are successfully routed to the `logs:dlq` Valkey stream and `XACK`ed in the main stream to prevent stalling.

---

## 💥 Step 2: Manual Kubernetes Chaos Drills

In the staging cluster, execute the following manual drills using `kubectl`.

### Drill A: Valkey/Redis StatefulSet Failure
1. **Action**: Delete the Valkey pod forcefully while a load test is running.
   ```bash
   kubectl delete pod logsentinel-redis-0 --force --grace-period=0
   ```
2. **Expected Behavior**:
   - `api` pods will return HTTP 503 instantly (failing fast).
   - `drain-worker` pods will emit `ConnectionError` logs and begin exponential backoff.
3. **Recovery Validation**: 
   - Once Valkey recovers, `drain-worker` must automatically resume consuming the `logs:stream` starting from the `0-0` pending entries list (PEL) to prevent message loss.
   - Drain3 state must re-hydrate automatically from the `drain3:state:snapshot` key.

### Drill B: TimescaleDB Network Partition
1. **Action**: Scale the database replica to 0, wait 30 seconds, and scale back to 1.
   ```bash
   kubectl scale statefulset logsentinel-postgresql --replicas=0
   sleep 30
   kubectl scale statefulset logsentinel-postgresql --replicas=1
   ```
2. **Expected Behavior**:
   - `drain-worker` will successfully parse logs but fail the `ParsedLogBatchManager` bulk insert.
   - Batch manager will hold the parsed logs in memory and initiate retry loops with exponential backoff.
3. **Recovery Validation**:
   - Once the DB returns, the `ParsedLogBatchManager` must flush the retained buffer successfully without losing logs.

### Drill C: Worker Pod Eviction & Auto-Restart
1. **Action**: Kill all active drain workers simultaneously.
   ```bash
   kubectl delete pods -l app.kubernetes.io/name=logsentinel-drain-worker
   ```
2. **Expected Behavior**:
   - Valkey `logs:stream` length will increase linearly as API ingestion continues.
3. **Recovery Validation**:
   - Kubernetes ReplicaSet controller spins up replacement workers.
   - New workers initialize the Drain3 parser and rapidly process the backlog.
   - The lag metric on the Grafana dashboard drops to 0.

---

## ✅ Day 2 Sign-Off Checklist
- [ ] `pytest tests/test_resilience_fault_injection.py` passes 100%.
- [ ] Manual Valkey recovery verified (no orphaned PEL messages).
- [ ] Manual DB partition recovery verified (batch manager retries succeeded).
- [ ] Dead-Letter Queue (`logs:dlq`) verified functional for poison pills.

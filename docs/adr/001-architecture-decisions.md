# ADR 001: Core Architecture Decisions for Real-Time Anomaly Detection, Dynamic Topology, and Blast Radius Ranking

**Status:** Accepted  
**Date:** 2026-08-17  
**Author:** Principal Software Engineer & Distributed Systems Architect  
**Deciders:** LogSentinel Engineering & Platform Architecture Team  

---

## 1. Context & Problem Statement

Modern microservice architectures generate millions of unstructured, heterogeneous log lines per minute across distributed services. Traditional monitoring paradigms suffer from three major shortcomings:
1. **Static Threshold Alerting:** Generates high alert fatigue, cannot capture subtle behavioral anomalies, and requires manual maintenance.
2. **Post-Hoc Root Cause Analysis:** Triaging cascading failures requires manual grepping across correlation IDs and dashboards, delaying Mean Time to Detection (MTTD) and Mean Time to Resolution (MTTR).
3. **High Latency / High Cost AI Ingestion:** Naive attempts to route high-volume log streams directly into Large Language Models (LLMs) incur prohibitive monetary costs ($0.001–$0.03 per 1k tokens) and unacceptable tail latency (500ms–3000ms per batch).

LogSentinel was engineered to provide an automated, real-time log anomaly detection, dynamic service topology mapping, and root-cause blast-radius ranking platform operating at high throughput with sub-second end-to-end latency.

This Architecture Decision Record (ADR) formalizes the rationale, evaluated alternatives, and trade-offs behind our four primary technical decisions.

---

## 2. Decision Summary

| Architectural Area | Chosen Technology / Pattern | Key Alternatives Evaluated | Primary Decision Drivers |
| :--- | :--- | :--- | :--- |
| **Ingestion & Buffering** | **Redis Streams / Valkey** | Apache Kafka, RabbitMQ | Sub-millisecond latency, minimal operational footprint, consumer groups with backpressure, shared cache state. |
| **Log Template Mining** | **Drain3 (Prefix-Tree Clustering)** | Direct LLM / GPT Extraction, Regex / Grok Parsers | 10,000+ logs/sec deterministic clustering, zero per-token cost, dynamic parameter masking. |
| **Anomaly Detection** | **Sliding-Window Isolation Forest** | Static Thresholds, LSTM / Autoencoders | Unsupervised outlier detection, resilience to non-stationary baselines, low CPU/memory inference footprint. |
| **Root Cause & Blast Radius** | **NetworkX Directed Graph Pathway Scorer** | Naive Correlation / Co-occurrence Matrix | Directional cascade propagation along true call-chains, hop distance weighting, deterministic ranking. |

---

## 3. Detailed Architectural Decisions

### 3.1. Ingestion & Buffering: Redis Streams / Valkey vs. Apache Kafka & RabbitMQ

#### Context
The ingestion layer must absorb spikes exceeding 10,000 logs/sec with sub-millisecond p99 enqueue latency and distribute work across asynchronous worker pools (Drain parser, Feature extractor, Topology pipeline).

#### Evaluated Alternatives
- **Apache Kafka:** Excellent durability and multi-datacenter partition replication. However, Kafka requires substantial memory overhead (JVM, ZooKeeper/KRaft), operational complexity, and tuning for high partition counts.
- **RabbitMQ (AMQP):** Mature message routing, but AMQP memory queue queues degrade under massive unconsumed backlog bursts.
- **Redis Streams / Valkey:** Lightweight, memory-first append-only log data structure supporting consumer groups (`XADD`, `XREADGROUP`, `XACK`), PEL (Pending Entry List) for at-least-once delivery, and sub-millisecond dispatch.

#### Decision
We selected **Redis Streams / Valkey**. Redis is already utilized for hot session cache and fast telemetry state sharing; using Redis Streams eliminates additional infrastructure dependencies while meeting all throughput and latency SLAs.

---

### 3.2. Log Parsing: Drain3 Template Mining vs. Direct LLM / Regex Parsing

#### Context
Unstructured log messages must be grouped into structural templates where variable fields (IPs, numbers, UUIDs, hex values) are masked as parameters `<*>`.

#### Evaluated Alternatives
- **Direct LLM Parsing (e.g., OpenAI / Gemini / Llama):**
  - *Pros:* High semantic understanding.
  - *Cons:* At 1,000 logs/sec, raw LLM parsing would cost ~$2,000+/day and introduce 400ms–2000ms latency per log, making real-time streaming impossible.
- **Static Regex / Grok Rules:**
  - *Pros:* Extremely fast.
  - *Cons:* Fragile; requires continuous manual rule authoring as application developers change log formats.
- **Drain3 (Fixed-Depth Parse Tree Mining):**
  - *Pros:* High throughput (15,000+ logs/sec/core), deterministic tree traversal, bounded memory footprint, zero external API costs, automatic extraction of dynamic parameters.

#### Decision
We selected **Drain3**. Drain3 serves as the fast deterministic front-line clustering engine. LLM intelligence is reserved for high-level incident summarization and remediation suggestion on the clustered templates rather than raw log ingestion.

---

### 3.3. Anomaly Detection: Sliding-Window Isolation Forest vs. Static Thresholds & Deep LSTM

#### Context
System anomalies manifest as non-linear combinations of error spikes, template distribution shifts, latency degradation, and transaction burst rates across time windows.

#### Evaluated Alternatives
- **Static Threshold Alerting:** Incapable of detecting multi-dimensional anomalies (e.g., subtle latency rises across low-volume background workers).
- **Deep LSTM / Transformer Autoencoders:** High compute requirements for continuous retraining and GPU dependency.
- **Sliding-Window Isolation Forest:**
  - Fast tree-based isolation of rare multi-dimensional feature vectors.
  - Generates normalized anomaly scores between 0.0 (nominal) and 1.0 (anomalous).
  - Trainable in CPU memory in < 200ms over thousands of window vectors.

#### Decision
We selected **Sliding-Window Isolation Forest**. The sliding window aggregates 12 statistical, template entropy, and service distribution metrics per window, feeding fixed-width feature vectors into the Isolation Forest detector.

---

### 3.4. Root Cause & Blast Radius: NetworkX Directed Graph Propagation vs. Naive Log Correlation

#### Context
When an incident occurs (e.g., `postgres-db` connection pool exhaustion), errors propagate upstream through `payment-gateway` and `order-service` to `api-gateway`. Naive correlation flags all services simultaneously, obscuring the original root cause.

#### Evaluated Alternatives
- **Temporal Co-occurrence Correlation:** Groups services that logged errors at the same timestamp. Fails to distinguish between the initiator and downstream victims.
- **NetworkX Directed Graph Pathway Scoring:**
  - Builds a real-time directed dependency graph $G = (V, E)$ from distributed tracing spans (`caller_service` $\rightarrow$ `callee_service`).
  - Evaluates root candidates by scoring topological predecessor pathways, temporal precedence (who failed first), and symptom consistency.
  - Computes exact blast radius trees with node classification (`root`, `direct`, `indirect`).

#### Decision
We selected **NetworkX Directed Graph Pathway Scoring**. This ensures deterministic root-cause isolation and actionable blast-radius ranking for site reliability engineers.

---

## 4. Consequences & Operational Guidelines

### Positive Consequences
1. **Low End-to-End Latency:** Time from log emission to anomaly scoring and blast radius graph update is under 500ms.
2. **Cost Efficiency:** Zero third-party inference costs during continuous log ingestion.
3. **High Resilience:** Decoupled worker consumers allow workers to restart or scale horizontally without log loss.
4. **Deterministic Reproducibility:** Graph pathway ranking and template clustering produce consistent, auditable outputs.

### Operational Trade-offs & Mitigations
- **Redis Memory Management:** Redis Stream lengths are capped using `MAXLEN ~` to prevent unbounded memory growth.
- **Cold Start Training:** The Isolation Forest detector maintains a fallback baseline threshold during warm-up periods before the model is fully trained.
- **Topology Eviction:** NetworkX in-memory graphs evict stale transaction traces beyond the configured retention window (default 1000 transactions).

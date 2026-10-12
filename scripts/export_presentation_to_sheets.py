"""
Export LogSentinel Presentation to Google Sheets compatible format (Excel .xlsx & CSV)
and generate Google Apps Script to build Google Slides automatically.
"""

import os
import csv
import json
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def build_presentation():
    # -------------------------------------------------------------
    # 1. Slide Deck Data (18 Slides matching templates)
    # -------------------------------------------------------------
    slides_data = [
        {
            "slide_num": 1,
            "title": "LogSentinel: Real-Time Observability & Blast-Radius Engine",
            "template_ref": "PlayMap Slide 1 / FlutterForecast Slide 1",
            "category": "Title & Vision",
            "headline": "Taming Microservice Chaos in Real Time",
            "visual_layout": "Cover Slide: Centered Logo emblem, Bold Title, Subtitle, Pill-shaped Slogan tag ('your journey starts here' style), Cyber-navy background with emerald/cyan neon accents.",
            "main_content": (
                "LOGSENTINEL\n"
                "Real-Time Unsupervised Log Anomaly Detection, Dynamic Topology Mapping & Root-Cause Blast-Radius Ranking\n\n"
                "• Autonomous Real-Time Stream Ingestion (10,000+ logs/sec)\n"
                "• Unsupervised ML Anomaly Detection (Drain3 + Isolation Forest)\n"
                "• Dynamic Microservice Causal Graph & Blast-Radius Ranking\n"
                "• High-Performance Full-Stack Fleet (FastAPI + Valkey + TimescaleDB + React 18)"
            ),
            "key_metrics": "10,000+ logs/sec | <120ms E2E Latency | 95%+ Compression Ratio",
            "design_style": "Background: #0B0F19 (Dark Cyber), Primary: #10B981 (Emerald), Secondary: #3B82F6 (Blue), Accent: #F97316 (Orange)",
            "speaker_notes": (
                "Good morning everyone. Today we are excited to introduce LogSentinel—an intelligent, high-throughput "
                "observability platform engineered to solve one of modern software engineering's biggest headaches: "
                "microservice chaos. In complex distributed clouds, when one component fails, everything seems to catch fire. "
                "LogSentinel ingests millions of logs in real time, mines semantic templates without human intervention, "
                "catches unseen anomalies with unsupervised machine learning, and maps the exact cascading blast radius "
                "so engineers know the true root cause within seconds."
            )
        },
        {
            "slide_num": 2,
            "title": "The Engineering Team",
            "template_ref": "PlayMap Slide 2 / FlutterForecast Slide 2",
            "category": "Team",
            "headline": "Meet the Minds Behind LogSentinel",
            "visual_layout": "5 Circular Profile Avatars horizontally aligned with Role Badges, Expertise, and Core Responsibilities.",
            "main_content": (
                "1. Melos Hajrullahu — Team Leader & ML Systems Architect\n"
                "   • Unsupervised Isolation Forest pipeline, threshold optimization, and algorithmic evaluation.\n\n"
                "2. Leorent Ismajli — Core Engine & Distributed Streaming Lead\n"
                "   • Valkey stream buffer, Drain3 online template miner, and async ingestion queue.\n\n"
                "3. Blerim Haxhiu — Frontend & Dynamic Graph Visualization Engineer\n"
                "   • React 18, Vite, Cytoscape.js & @xyflow/react dynamic service topology dashboard.\n\n"
                "4. Blert Sylejmani — Backend API & Time-Series Database Specialist\n"
                "   • FastAPI endpoints, TimescaleDB hypertables, WebSocket telemetry broadcast.\n\n"
                "5. Juled Morina — SRE, Chaos Engineering & Production Hardening\n"
                "   • Synthetic chaos injection, Docker Compose fleet orchestration, and benchmark validation."
            ),
            "key_metrics": "5 Core Engineers | 100+ Tests Passing | Multi-Tenant Architecture",
            "design_style": "5 Circular portrait frames with gold/emerald ring borders, role pill badges, clean typography.",
            "speaker_notes": (
                "Our team brings together distributed systems engineering, streaming data infrastructure, machine learning, "
                "and modern UI visualization. Melos spearheaded our ML pipeline and statistical threshold sweeps; "
                "Leorent architected our ultra-fast Valkey and Drain3 log ingestion pipeline; Blerim built our real-time "
                "interactive React 18 topology graph; Blert optimized the TimescaleDB hypertables and FastAPI telemetry; "
                "and Juled designed our chaos engineering and capacity benchmarks."
            )
        },
        {
            "slide_num": 3,
            "title": "The Problem: Microservice Chaos & Observability Blindspots",
            "template_ref": "PlayMap Slide 3 (3 Numbered Cards) & FlutterForecast Slide 3",
            "category": "Problem",
            "headline": "Why Modern Distributed Cloud Observability Fails",
            "visual_layout": "3 Numbered Rectangular Cards (1, 2, 3) with bold accent headers, icons, and symptom descriptions.",
            "main_content": (
                "[Card 1] Microservice Complexity & Cascading Outages\n"
                "• Cloud systems consist of dozens of decoupled services. When a database or auth service stalls, failures cascade down the dependency chain.\n"
                "• Unstructured logs explode into millions of noisy lines, obscuring the primary fault.\n\n"
                "[Card 2] Alert Fatigue & False Alarms\n"
                "• SREs are inundated with hundreds of disjointed alerts from downstream victims (e.g. 504 timeouts in UI, payment retries, queue backlogs).\n"
                "• Over 70% of alerts during an incident are symptoms, not root causes.\n\n"
                "[Card 3] High MTTR & Staggering Downtime Cost\n"
                "• Manual log grep and correlation takes 45 to 90 minutes on average.\n"
                "• Enterprise downtime costs average $5,600+ per minute ($300,000+ per hour), inflicting severe SLA penalties and brand damage."
            ),
            "key_metrics": "$5,600/min Downtime Cost | 70%+ Symptom Noise | 45-90 min Average MTTR",
            "design_style": "3 Colored Cards: #F97316 (Orange - Disruption), #EF4444 (Red - Alert Fatigue), #6366F1 (Indigo - MTTR Cost)",
            "speaker_notes": (
                "Let's look at the reality of modern cloud engineering. As monolithic applications were broken into microservices, "
                "observability became a nightmare. In a cascade failure, dozens of services light up red simultaneously. SREs suffer "
                "from severe alert fatigue. Because teams spend an hour just figuring out WHICH service died first, downtime costs "
                "spiral out of control. We built LogSentinel to automate this entire triage loop."
            )
        },
        {
            "slide_num": 4,
            "title": "Data-Driven Research & Industry Benchmarks",
            "template_ref": "PlayMap Slide 4 / FlutterForecast Slide 3",
            "category": "Research & Data",
            "headline": "Empirical Evidence & Operational Scale Challenges",
            "visual_layout": "Boxed statistical card layout featuring 4 oversized metric callouts and verified empirical benchmark results.",
            "main_content": (
                "INDUSTRY RESEARCH DATA:\n"
                "• 82% of cloud production outages are triggered by complex interconnected microservice cascades.\n"
                "• 67% of Mean Time to Resolution (MTTR) is consumed merely finding the root-cause service.\n"
                "• 52% of on-call engineers report burnout from alert fatigue and noisy false positives.\n\n"
                "LOGSENTINEL EMPIRICAL VALIDATION:\n"
                "• 10,000+ logs/sec sustained ingestion per single worker node without backpressure.\n"
                "• 95%+ log compression achieved online by Drain3 template discovery.\n"
                "• 0.959 ROC-AUC and 0.839 F1-score in detecting zero-day anomalies across 5,000 production incident records."
            ),
            "key_metrics": "82% Cascading Outages | 67% MTTR Wasted Locating Fault | 0.959 ROC-AUC Score",
            "design_style": "Large data callout stat blocks with glowing green borders on dark background.",
            "speaker_notes": (
                "Our design is grounded in rigorous research. Studies show 82% of outages are cascades and two-thirds of triage "
                "time is wasted searching for the root-cause service. We rigorously benchmarked LogSentinel on real failure patterns—"
                "including SQL injection, thread starvation, latency spikes, and brute force attacks. Our Drain3 miner compresses logs "
                "by over 95%, while our tuned Isolation Forest delivers a 0.959 ROC-AUC and an 83.9% F1-score."
            )
        },
        {
            "slide_num": 5,
            "title": "The LogSentinel Solution: Autonomous Triad",
            "template_ref": "PlayMap Slide 5 (3 Numbered Solution Cards) & FlutterForecast Slide 4",
            "category": "Solution",
            "headline": "Stream Processing + Unsupervised ML + Causal Graph Theory",
            "visual_layout": "3 Numbered Pillar Cards (1, 2, 3) showing the three architectural breakthroughs of LogSentinel.",
            "main_content": (
                "[Pillar 1] Real-Time Streaming Ingestion & High-Density Compression\n"
                "• Ingests standard OTLP, Fluent Bit, and Vector logs via async Valkey stream buffer.\n"
                "• Drain3 tree-based online template mining compresses raw strings into parameterized clusters in sub-milliseconds.\n\n"
                "[Pillar 2] Unsupervised ML Anomaly Detection\n"
                "• Sliding-window feature vectors (volume, template entropy, error ratios, burst velocity).\n"
                "• Unsupervised Isolation Forest catches unknown zero-day anomalies without training labels or manual regex rules.\n\n"
                "[Pillar 3] Dynamic Causal Topology & Blast-Radius Ranking\n"
                "• Reconstructs service dependency topology dynamically using NetworkX directed causal graphs.\n"
                "• Isolates the upstream root cause and calculates quantitative blast-radius impact within <120ms."
            ),
            "key_metrics": "Zero Manual Rules | O(1) Memory Ingest Buffer | Sub-120ms Incident Broadcast",
            "design_style": "3 Modern Cards with numbered badges: 1 Teal (#0D9488), 2 Blue (#2563EB), 3 Purple (#7C3AED)",
            "speaker_notes": (
                "LogSentinel solves this crisis with a unique 3-pillar architecture. Pillar 1: We ingest up to 10,000+ logs/second "
                "into a high-speed memory buffer and compress them into structured templates online. Pillar 2: We use an unsupervised "
                "Isolation Forest running across sliding windows to detect anomalies—meaning zero manual regex maintenance. "
                "Pillar 3: We feed anomalous windows into a dynamic causal graph that immediately flags the root-cause service and "
                "ranks its blast radius."
            )
        },
        {
            "slide_num": 6,
            "title": "Engineering Lifecycle & Solution Phases",
            "template_ref": "FlutterForecast Slide 6 (5 Milestone Step Circles)",
            "category": "Lifecycle / Roadmap",
            "headline": "From Algorithmic Theory to Production Hardening",
            "visual_layout": "5 Connected Horizontal Milestone Circles (1 -> 2 -> 3 -> 4 -> 5) with progress connector line.",
            "main_content": (
                "Phase 01: Intensive Research & Algorithmic Benchmarking\n"
                "• Comparative evaluation of unsupervised models (Isolation Forest vs LOF vs Autoencoders).\n"
                "• Parameter tuning for Drain3 depth and similarity threshold.\n\n"
                "Phase 02: Data Gathering & Protocol Ingestion\n"
                "• Native OTLP (/v1/logs), Fluent Bit HTTP sink, Vector sink, and Python SDK.\n"
                "• Tenant-scoped API key authorization and rate limiting.\n\n"
                "Phase 03: ML & Causal Engine Development\n"
                "• 60s sliding window with 30s stride generating 12-dimensional feature vectors.\n"
                "• NetworkX directed graph propagation for blast-radius scoring.\n\n"
                "Phase 04: Full-Stack Platform Development\n"
                "• Asynchronous FastAPI backend + TimescaleDB partitioned hypertables.\n"
                "• React 18, Vite, Cytoscape.js & @xyflow/react live WebSocket dashboard.\n\n"
                "Phase 05: Chaos Testing & Production Hardening\n"
                "• Automated chaos injection suite (latency spikes, DB pool exhaustion, SQLi).\n"
                "• Concurrency stress benchmarks, CI/CD linting, and Docker Compose fleet."
            ),
            "key_metrics": "5 End-to-End Milestones | 100% Turnkey Deployment via Docker Compose",
            "design_style": "Horizontal progress track with glowing circular milestone nodes (Emerald, Cyan, Blue, Violet, Amber).",
            "speaker_notes": (
                "Just like FlutterForecast's structured phases, LogSentinel progressed through 5 disciplined engineering milestones: "
                "First, algorithmic research on Drain3 and Isolation Forests. Second, universal protocol ingestion supporting OTLP "
                "and Fluent Bit. Third, developing our sliding-window feature extraction and causal graph scoring. Fourth, building "
                "the reactive full-stack platform with FastAPI and React 18. And fifth, stress-testing under simulated chaos incidents."
            )
        },
        {
            "slide_num": 7,
            "title": "Technologies: Front-End Architecture",
            "template_ref": "PlayMap Slide 6 (3 Vertical Color Columns)",
            "category": "Technology Stack",
            "headline": "Modern, Reactive, High-Performance Visualization",
            "visual_layout": "3 Vertical Color Columns (Column 1: Light Cream/White, Column 2: Amber/Gold, Column 3: Sky Blue).",
            "main_content": (
                "[Column 1] React 18 & Vite 6\n"
                "• Component-driven UI architecture with concurrent rendering.\n"
                "• Ultra-fast Vite HMR build system with sub-second feedback.\n"
                "• TypeScript strict type safety across all telemetry models.\n\n"
                "[Column 2] TailwindCSS & Motion\n"
                "• Cyberpunk-sleek dark mode design system (#0B0F19 background).\n"
                "• Responsive glassmorphism with high-contrast status badges.\n"
                "• Micro-animations for live anomaly alerts and pulsing nodes.\n\n"
                "[Column 3] Cytoscape.js & @xyflow/react\n"
                "• Dynamic topological graph rendering for distributed microservices.\n"
                "• Real-time node coloring (Green=Healthy, Amber=Degraded, Red=Root Cause).\n"
                "• Interactive blast-radius click-through and dependency path tracing."
            ),
            "key_metrics": "60 FPS Graph Rendering | Sub-second Live WebSocket Updates | Fully Responsive",
            "design_style": "3 Vertical columns with bold headers, technology badges, and capability lists.",
            "speaker_notes": (
                "Looking at our frontend stack: We chose React 18 with Vite for maximum rendering performance and instantaneous "
                "HMR. TailwindCSS and Framer Motion provide an intuitive, high-contrast dark mode interface with micro-animations. "
                "For the topology visualizer, we integrated Cytoscape.js and XYFlow, allowing SREs to explore dynamic dependency trees "
                "rendered at 60 frames per second with instant visual indicators during an incident."
            )
        },
        {
            "slide_num": 8,
            "title": "Technologies: Back-End & Data Pipeline",
            "template_ref": "PlayMap Slide 7 (3 Vertical Color Columns)",
            "category": "Technology Stack",
            "headline": "High-Throughput, Low-Latency Asynchronous Core",
            "visual_layout": "3 Vertical Color Columns (Column 1: Rose/Salmon, Column 2: Amber/Orange, Column 3: Electric Blue).",
            "main_content": (
                "[Column 1] Python 3.11+ & FastAPI\n"
                "• Asynchronous ASGI event loop handling high-concurrency ingestion.\n"
                "• Strict Pydantic v2 data validation schemas.\n"
                "• Native WebSocket server (/ws/telemetry) for real-time pushing.\n\n"
                "[Column 2] Valkey 8.0 & Redis Streams\n"
                "• High-throughput XADD ingestion buffer ensuring zero HTTP blocking.\n"
                "• Decoupled asynchronous worker consumer groups.\n"
                "• In-memory caching for tenant rate-limiting and session validation.\n\n"
                "[Column 3] TimescaleDB & PostgreSQL\n"
                "• Hypertables optimized for petabyte-scale time-series log persistence.\n"
                "• Continuous aggregates for real-time service health analytics.\n"
                "• Strict tenant isolation via composite foreign-key database constraints."
            ),
            "key_metrics": "O(1) Valkey Ingestion | Sub-5ms Hypertable Writes | Zero-Copy Async Streaming",
            "design_style": "3 Vertical columns with bold back-end logos, architecture tags, and performance specs.",
            "speaker_notes": (
                "On the backend, Python 3.11 and FastAPI power our asynchronous ingestion gateway. We decouple ingestion from processing "
                "using Valkey 8.0 streams—giving us zero backpressure even during log spikes. For persistence, TimescaleDB hypertables "
                "store billions of time-series log records with continuous aggregates, allowing rapid analytical lookups without degrading "
                "operational performance."
            )
        },
        {
            "slide_num": 9,
            "title": "End-to-End System Architecture & Streaming Pipeline",
            "template_ref": "FlutterForecast Slide 11 & README Mermaid Sequence",
            "category": "Architecture",
            "headline": "How Logs Flow from Collectors to Root-Cause Pinpointing",
            "visual_layout": "Pipelined Flow Diagram: 7 Interconnected Stages with arrows, queue buffers, and latency tags.",
            "main_content": (
                "1. COLLECTORS: Fluent Bit, Vector, OpenTelemetry (OTLP), and Python SDK push logs to /v1/logs & /ingest-log.\n"
                "2. ASYNC INGESTION: FastAPI validates API key and immediately writes to Valkey stream (XADD) in <2ms.\n"
                "3. DRAIN3 MINING: Background worker streams logs through Drain3 tree miner, discovering templates and masking variables [<*>].\n"
                "4. WINDOW EXTRACTION: 60s sliding window (30s stride) computes 12 features (entropy, error ratios, rate velocity).\n"
                "5. ISOLATION FOREST: Pre-trained scikit-learn model evaluates window vector against optimal threshold (-0.075).\n"
                "6. CAUSAL GRAPH: NetworkX engine traverses topological dependencies, computing blast radius and ranking root cause.\n"
                "7. WEBSOCKET TELEMETRY: Broadcasts incident alerts, parsed logs, and topology updates to React dashboard (<120ms p99)."
            ),
            "key_metrics": "<120ms End-to-End Latency | 7 Processing Stages | Completely Non-Blocking",
            "design_style": "Flow diagram with sequential numbered pill nodes, pipeline arrows, and glowing status tags.",
            "speaker_notes": (
                "Here is how data flows through LogSentinel end-to-end: Logs arrive via OTLP or Fluent Bit into FastAPI. "
                "Instead of hitting the database immediately, they land in an in-memory Valkey stream. Our Drain3 worker parses them "
                "into templates on the fly. The feature extractor groups them into 60-second sliding windows. Our Isolation Forest "
                "scores the window; if anomalous, the NetworkX graph engine ranks which upstream service triggered the cascade. "
                "The entire sequence takes under 120 milliseconds."
            )
        },
        {
            "slide_num": 10,
            "title": "Algorithmic Core: Drain3 & Sliding-Window Isolation Forest",
            "template_ref": "FlutterForecast Slides 12-13 (Algorithmic & System Components)",
            "category": "ML & Algorithm",
            "headline": "Unsupervised Intelligence Without Manual Regex Rules",
            "visual_layout": "3 Numbered System Component Boxes (01 Matrix/Miner, 02 Model Training, 03 Inference/Scoring) matching FlutterForecast Slide 13.",
            "main_content": (
                "[01] Online Template Mining (Drain3)\n"
                "• Parses raw unstructured text into structured log templates in <0.5ms.\n"
                "• Dynamically detects tokens, replaces variables with [<*>], and updates cluster prefix trees.\n"
                "• Delivers 95%+ data compression while retaining semantic forensic fidelity.\n\n"
                "[02] Sliding-Window Feature Extractor\n"
                "• 60s windows with 30s stride extract 12 numerical & Shannon entropy features:\n"
                "  - Statistical: log_count, error_count, warning_count, error_ratio, logs_per_sec\n"
                "  - Entropy & Diversity: template_entropy, unique_templates, dominant_template_ratio\n"
                "  - Topology: active_services, dominant_service_count, burst_indicator\n\n"
                "[03] Unsupervised Isolation Forest & Threshold Sweep\n"
                "• Ensemble of 100 isolation trees isolating anomalies based on path length.\n"
                "• Optimal threshold sweep (-0.075): 0.940 Accuracy, 0.812 Precision, 0.867 Recall, 0.959 ROC-AUC."
            ),
            "key_metrics": "0.959 ROC-AUC | 0.839 F1-Score | 12 Vector Features | 100 Trees",
            "design_style": "3 Numbered component cards (01, 02, 03) with mathematical equation callouts and threshold curves.",
            "speaker_notes": (
                "Our algorithmic core has 3 major components: Component 1 is Drain3, an online parse tree that strips dynamic "
                "variables and produces clean templates. Component 2 is our 12-feature sliding window extractor measuring "
                "Shannon entropy, error acceleration, and service distribution. Component 3 is our Isolation Forest. "
                "Through an exhaustive threshold sweep, we identified -0.075 as the optimal cutoff, achieving an outstanding "
                "0.959 ROC-AUC and 83.9% F1-score with near-zero false alarms."
            )
        },
        {
            "slide_num": 11,
            "title": "Dynamic Causal Topology & Blast-Radius Ranking",
            "template_ref": "FlutterForecast Slide 12 & PlayMap Slide 8",
            "category": "Graph Engine",
            "headline": "Tracing Cascading Failures to Their True Origin",
            "visual_layout": "Split Layout: Left side shows Directed Causal Graph with root node colored red; Right side shows Blast-Radius ranking table.",
            "main_content": (
                "DYNAMIC TOPOLOGY AUTODISCOVERY:\n"
                "• Inter-service HTTP and gRPC request traces automatically populate directed graph edges in NetworkX.\n"
                "• No manual architecture diagrams or static dependency definitions required.\n\n"
                "ROOT-CAUSE VS. VICTIM SEPARATION:\n"
                "• Temporal Lead-Time Analysis: Detects which service showed initial degradation before downstream errors occurred.\n"
                "• Downstream Symptom Suppression: Automatically silences redundant 504 Gateway Timeouts from dependent services.\n\n"
                "QUANTITATIVE BLAST-RADIUS SCORING:\n"
                "• Scores the reachability of downstream services from the failed node.\n"
                "• Computes percentage of dependent microservice fleet impacted (e.g. 66% blast radius across Order & Payment services)."
            ),
            "key_metrics": "100% Causal Dependency Autodiscovery | 85%+ Redundant Alert Suppression",
            "design_style": "Cytoscape-inspired graph visualization mockup with highlighted path traversal and blast-radius gauge.",
            "speaker_notes": (
                "When a failure occurs, the hardest question is: 'Who started it?' Downstream services throw errors simply because "
                "their upstream calls timed out. LogSentinel builds a dynamic directed graph of your services. By combining "
                "temporal anomaly onset with dependency topology, it separates the root-cause culprit from innocent victim services, "
                "suppressing over 85% of downstream symptom alerts."
            )
        },
        {
            "slide_num": 12,
            "title": "Problem-Solution Fit Matrix",
            "template_ref": "PlayMap Slide 9 (Mapped Rows with Detailed Paragraphs)",
            "category": "Validation",
            "headline": "Directly Mapping Operational Challenges to LogSentinel Capabilities",
            "visual_layout": "3 Horizontal Rows: Left: Problem Pill -> Right: Solution Pill, with descriptive comparison paragraphs.",
            "main_content": (
                "[Row 1] Unknown Failures & Zero-Day Bugs  --->  Unsupervised Isolation Forest\n"
                "• Problem: Traditional monitoring depends on regex pattern rules. If an engineer doesn't write a rule for a new exception type, it goes undetected.\n"
                "• LogSentinel Fit: Unsupervised Isolation Forest evaluates structural anomalies and entropy shifts, flagging zero-day failure modes without pre-labeled training.\n\n"
                "[Row 2] Cascading Outages & Alert Fatigue  --->  Dynamic Causal Topology Ranking\n"
                "• Problem: An auth service database failure causes 20 downstream microservices to alert simultaneously, drowning SREs in symptom noise.\n"
                "• LogSentinel Fit: NetworkX graph traversal isolates the upstream root node (auth-service) and ranks blast radius, suppressing symptom alarms.\n\n"
                "[Row 3] Ballooning Log Storage Costs  --->  Drain3 Real-Time Template Mining\n"
                "• Problem: Storing and indexing terabytes of repetitive raw log strings causes exorbitant cloud bills and sluggish dashboard queries.\n"
                "• LogSentinel Fit: Drain3 clusters logs into templates, storing only dynamic parameters. Achieves 95%+ data compression while preserving full diagnostic context."
            ),
            "key_metrics": "95%+ Storage Compression | Zero-Day Anomaly Detection | Instant Root-Cause Pinpointing",
            "design_style": "3 Stacked cards with Problem -> Solution arrows, colored icons, and structured text paragraphs.",
            "speaker_notes": (
                "This matrix demonstrates our precise problem-solution fit. For unknown bugs: our unsupervised model catches anomalies "
                "without regex rules. For cascading outages: our causal graph points straight to the root cause and silences symptom "
                "alerts. And for soaring cloud logging costs: Drain3 compresses logs by 95% while keeping all diagnostic fidelity."
            )
        },
        {
            "slide_num": 13,
            "title": "Live Demonstration & Chaos Engineering Scenario",
            "template_ref": "PlayMap Slide 8 ('DEMO' Cycle) & FlutterForecast Slide 14",
            "category": "Demonstration",
            "headline": "Real-Time Detection of Cascading Microservice Failure",
            "visual_layout": "4-Step Circular Execution Cycle (Baseline Traffic -> Chaos Injection -> ML Anomaly Detection -> Topology Highlight & Resolution).",
            "main_content": (
                "DEMONSTRATION WORKFLOW (scripts/trigger_demo_incident.py & scripts/demo_live_pipeline.py):\n\n"
                "Step 1: Normal Baseline Traffic\n"
                "• 500 logs/sec streaming across auth-service, payment-service, and order-service via /ingest-log.\n"
                "• Topology graph shows all green nodes; Isolation Forest anomaly score stays comfortably below -0.075.\n\n"
                "Step 2: Simulated Chaos Injection\n"
                "• Chaos script injects database latency spike and connection pool exhaustion into auth-service.\n"
                "• Downstream order-service and payment-service begin logging HTTP 504 timeouts.\n\n"
                "Step 3: Real-Time Algorithmic Reaction\n"
                "• Drain3 flags new cluster templates; sliding-window error_ratio spikes past 0.15; Isolation Forest flags anomaly.\n\n"
                "Step 4: Real-Time UI Incident Broadcast\n"
                "• WebSocket broadcasts alert to React dashboard: auth-service node turns flashing red, ranked #1 root cause with 66% blast radius!"
            ),
            "key_metrics": "<120ms Broadcast Delay | 100% Synthetic Chaos Repeatability | Interactive Live UI",
            "design_style": "Circular 4-stage process diagram with screenshot callout of the React 18 Cytoscape dashboard.",
            "speaker_notes": (
                "In our live demo, we simulate a realistic microservice outage. We run our chaos injection script, which triggers "
                "database pool exhaustion in the auth-service. As payment-service and order-service begin throwing timeout errors, "
                "LogSentinel's feature worker detects the entropy anomaly within seconds. On the React dashboard, the auth-service node "
                "instantly turns red, flagged as the root cause with a 66% blast radius."
            )
        },
        {
            "slide_num": 14,
            "title": "Added Value & Strategic ROI Breakdown",
            "template_ref": "PlayMap Slide 10 (20% Pie / Wheel Chart) & FlutterForecast Slide 15",
            "category": "Value Proposition",
            "headline": "Quantifiable Value Delivered Across 5 Strategic Pillars",
            "visual_layout": "Donut / Pie Chart divided into 5 equal 20% segments with glowing accent colors and central emblem.",
            "main_content": (
                "PROJECT FOCUS:\n"
                "LogSentinel delivers an integrated, autonomous observability experience that combines streaming ingestion, "
                "unsupervised ML, and dynamic graph theory directly from raw logs.\n\n"
                "STRATEGIC VALUE DISTRIBUTION (20% Each):\n"
                "• 20% MTTR Reduction: Slashes incident investigation time from 45+ minutes to under 30 seconds.\n"
                "• 20% Alert Fatigue Suppression: Filters out up to 85% of redundant cascading noise to focus on the root trigger.\n"
                "• 20% Zero-Configuration ML: Unsupervised learning eliminates brittle rules, manual regex, and model retraining overhead.\n"
                "• 20% High-Throughput Cloud Efficiency: Ingests 10,000+ logs/sec per node with ultra-low memory footprint (<52MB RSS).\n"
                "• 20% Dynamic Topology Autodiscovery: Automatically maps evolving service architectures without manual documentation."
            ),
            "key_metrics": "5 Strategic Value Pillars (20% Each) | 85% Alert Reduction | 90% MTTR Reduction",
            "design_style": "5-slice colorful Donut Chart: MTTR (Emerald), Noise (Cyan), Unsupervised ML (Blue), Scale (Violet), Topology (Amber).",
            "speaker_notes": (
                "LogSentinel's added value is evenly balanced across 5 critical pillars, each contributing 20% to the overall ROI: "
                "First, a 90% reduction in MTTR. Second, suppressing alert fatigue. Third, zero-configuration machine learning that "
                "needs no manual rules. Fourth, extreme cloud efficiency running at 10k logs/sec with under 52 megabytes of RAM. "
                "And fifth, dynamic service topology discovered automatically."
            )
        },
        {
            "slide_num": 15,
            "title": "Business Model: Tiered Platform Pricing",
            "template_ref": "PlayMap Slide 11 (3 Tiered Subscription Cards) & FlutterForecast Slide 5",
            "category": "Business Model",
            "headline": "Scalable Monetization for Teams of Every Size",
            "visual_layout": "3 Pricing Cards: Free Community ($0), LogSentinel Pro ($49/node/mo), Enterprise Sentinel ($1,499+/mo Custom).",
            "main_content": (
                "[Tier 1] COMMUNITY EDITION — Free & Open Source (Apache 2.0)\n"
                "• Full Docker Compose turnkey fleet (FastAPI + Valkey + TimescaleDB + React)\n"
                "• Drain3 online template miner & basic Isolation Forest\n"
                "• Up to 1,000 logs/second ingestion limit\n"
                "• Community forum & GitHub issue support\n\n"
                "[Tier 2] LOGSENTINEL PRO — $49 / node / month\n"
                "• Everything in Community, plus:\n"
                "• High-throughput Valkey stream cluster (up to 25,000 logs/sec/node)\n"
                "• Dynamic Causal Graph & Blast-Radius Ranking engine\n"
                "• Multi-tenant data isolation & automated model retraining\n"
                "• Slack, PagerDuty, and Webhook alert integrations\n\n"
                "[Tier 3] ENTERPRISE SENTINEL — $1,499+ / month (Custom SLA)\n"
                "• Everything in Pro, plus:\n"
                "• Enterprise SSO (Microsoft Entra ID & Google OAuth 2.0 native)\n"
                "• eBPF kernel-level distributed tracing collector\n"
                "• Air-gapped / On-Premise VPC deployment support\n"
                "• 24/7 Dedicated SRE support & 99.99% uptime SLA"
            ),
            "key_metrics": "Free Open-Source Core | $49/node Pro Tier | $1,499+ Enterprise Contract",
            "design_style": "3 Tiered cards: Community (Subtle Gray), Pro (Featured Emerald with 'POPULAR' ribbon), Enterprise (Deep Indigo).",
            "speaker_notes": (
                "Our business model blends product-led open-source adoption with high-margin enterprise licensing: "
                "Our Community Edition is open source under Apache 2.0, giving developers instant local adoption. "
                "LogSentinel Pro at $49 per node per month unlocks our causal graph, multi-tenancy, and automated retraining. "
                "Enterprise Sentinel starts at $1,499/month, adding native Microsoft and Google SSO, eBPF tracing, air-gapped deployment, "
                "and 24/7 SLAs."
            )
        },
        {
            "slide_num": 16,
            "title": "Business Model: Add-ons & Professional Services",
            "template_ref": "PlayMap Slide 12 (In-Website Shop & Boosters / Study Tools)",
            "category": "Business Model",
            "headline": "Modular Boosters & High-Value Enterprise Extensions",
            "visual_layout": "2 Structured Columns: Left: Cloud Boosters & Connectors ($199-$499/mo); Right: Enterprise Advisory & Services ($2,500-$10,000).",
            "main_content": (
                "[Category 1] Cloud Boosters & Integrations ($199 - $499 / mo)\n"
                "• Datadog & Splunk Bi-Directional Bridge: Forward parsed templates into existing enterprise SIEMs.\n"
                "• CloudWatch & GCP Cloud Logging Native Sinks: 1-click cloud audit log ingestion.\n"
                "• Long-Term Cold Storage Compactor: S3/GCS Parquet archiving with 99% query compression.\n"
                "• High-Frequency Anomaly Retraining Packs: Hourly adaptive model updates for high-churn clusters.\n\n"
                "[Category 2] SRE Advisory & Professional Services ($2,500 - $10,000)\n"
                "• Custom Domain Anomaly Calibration: Tuning Isolation Forest contamination for proprietary protocols.\n"
                "• Chaos Engineering Readiness Audit: Running simulated failure game days to validate resilience.\n"
                "• Production Cutover Assistance: 7-day guided rollout with zero downtime migration runbooks."
            ),
            "key_metrics": "$199-$499 Recurring Add-ons | $2.5k-$10k High-Margin Services | Expand Net Revenue",
            "design_style": "2 Card boxes with shopping/plug icons, pricing tags, and enterprise service checklists.",
            "speaker_notes": (
                "To further monetize and support enterprise accounts, we offer modular add-ons and advisory services. "
                "Cloud Boosters range from $199 to $499 a month for cold-storage S3 compactors and SIEM bridges. "
                "For mission-critical deployments, our team provides SRE advisory services—calibrating custom models and conducting "
                "chaos game days ranging from $2,500 to $10,000 per engagement."
            )
        },
        {
            "slide_num": 17,
            "title": "Future Roadmap & Innovation Horizon",
            "template_ref": "FlutterForecast Slide 16 ('Future Plans')",
            "category": "Roadmap",
            "headline": "The Next Frontier: Autonomous Self-Healing Observability",
            "visual_layout": "3 Sequential Future Milestone Cards with targeted release timelines (Q1 2027, Q2 2027, Q3 2027).",
            "main_content": (
                "Milestone 01: eBPF Kernel-Level Distributed Tracing (Q1 2027)\n"
                "• Zero-overhead kernel socket tracing bypassing application code modification.\n"
                "• Dynamic protocol correlation across gRPC, HTTP/2, and database wire protocols.\n\n"
                "Milestone 02: Generative AI Incident Post-Mortems & PR Generation (Q2 2027)\n"
                "• Autonomous LLM synthesis of anomaly windows into executive-ready incident post-mortems.\n"
                "• Automated draft pull requests suggesting configuration tweaks or bug fixes based on stack traces.\n\n"
                "Milestone 03: Autonomous Kubernetes Self-Healing Operator (Q3 2027)\n"
                "• Closed-loop remediation: automatically restart pods, shed traffic, or activate circuit breakers when anomaly confidence > 95%.\n"
                "• Eliminates human intervention for known cascading failure patterns."
            ),
            "key_metrics": "Zero-Code eBPF Tracing | Automated GenAI Post-Mortems | Closed-Loop Auto-Remediation",
            "design_style": "3 Modern milestone cards with horizon timeline arrows, AI/Kubernetes icons, and delivery targets.",
            "speaker_notes": (
                "Looking to the future, our roadmap expands LogSentinel into an autonomous self-healing platform: "
                "In Q1 2027, we introduce zero-overhead eBPF kernel tracing. In Q2, Generative AI incident post-mortems will automatically "
                "write incident reports and draft GitHub PRs. And in Q3, our Kubernetes Operator will close the loop with automated "
                "remediation—restarting failed pods and circuit breaking before customers even notice."
            )
        },
        {
            "slide_num": 18,
            "title": "Thank You & Open Q&A",
            "template_ref": "PlayMap Slide 13 / FlutterForecast Slide 17",
            "category": "Closing",
            "headline": "Taming Microservice Chaos Starts Today",
            "visual_layout": "Closing Slide: Bold 'Thank You', Slogan Banner, Team Contacts, GitHub & Documentation Links.",
            "main_content": (
                "THANK YOU FOR LISTENING!\n\n"
                "\"Change starts with the steps we take today!\"\n"
                "Empower your SREs. Eliminate alert noise. Tame microservice chaos.\n\n"
                "PROJECT RESOURCES & LINKS:\n"
                "• GitHub Repository: github.com/meloshaj/LogSentinel\n"
                "• Open Source License: Apache 2.0\n"
                "• Documentation: /docs (Runbooks, Feature Extraction, Architecture ADRs)\n"
                "• Quickstart: docker compose -f docker-compose.demo.yml up --build -d\n\n"
                "CONTACT THE TEAM:\n"
                "• Melos Hajrullahu (Team Lead): hajrullahumelos@gmail.com\n"
                "• Leorent Ismajli, Blerim Haxhiu, Blert Sylejmani, Juled Morina"
            ),
            "key_metrics": "Apache 2.0 Open Source | github.com/meloshaj/LogSentinel | Ready for Production",
            "design_style": "High-impact closing banner with vibrant blue/orange starburst accents, contact cards, and GitHub QR placeholder.",
            "speaker_notes": (
                "Thank you so much for your time. LogSentinel is fully open source under the Apache 2.0 license, with Docker Compose "
                "scripts ready to spin up right now. We invite you to clone the repo, run our chaos benchmark script, and see the future "
                "of autonomous observability in action. We are now open for any questions!"
            )
        }
    ]

    # -------------------------------------------------------------
    # 2. Problem-Solution Fit Matrix Data
    # -------------------------------------------------------------
    fit_matrix_data = [
        {
            "category": "Failure Detection",
            "challenge": "Zero-day / unknown anomalies go undetected",
            "traditional_flaw": "Requires pre-written static regex rules and threshold alert configs.",
            "logsentinel_solution": "Unsupervised Isolation Forest on 12-dimensional sliding windows.",
            "algorithmic_mechanism": "Detects anomalous feature vectors (entropy, error ratio, bursts) without pre-labeled data.",
            "business_impact": "Catches novel incidents before customers report outages; eliminates rule maintenance."
        },
        {
            "category": "Incident Triage",
            "challenge": "Cascading outages create overwhelming alert storms",
            "traditional_flaw": "Every downstream microservice alerts on 504 timeouts, blinding SREs.",
            "logsentinel_solution": "Dynamic Causal Topology Graph & Blast-Radius Ranking.",
            "algorithmic_mechanism": "NetworkX directed graph traversal with temporal lead-time analysis.",
            "business_impact": "Cuts 85%+ redundant symptom noise; isolates the single root-cause service instantly."
        },
        {
            "category": "Cloud Operating Cost",
            "challenge": "Exorbitant log indexing and storage bills",
            "traditional_flaw": "Indexing raw strings in Elasticsearch/Splunk requires massive RAM and disk.",
            "logsentinel_solution": "Drain3 Streaming Online Template Miner.",
            "algorithmic_mechanism": "Prefix-tree clustering replaces dynamic variables with [<*>] wildcards.",
            "business_impact": "95%+ log compression ratio; slashes cloud storage and memory footprint by up to 90%."
        },
        {
            "category": "Investigation Speed",
            "challenge": "High Mean Time to Resolution (MTTR = 45-90 min)",
            "traditional_flaw": "SREs manually query disparate log tools and piece together timelines.",
            "logsentinel_solution": "Sub-120ms Real-Time WebSocket Telemetry & Visual Dashboard.",
            "algorithmic_mechanism": "FastAPI WebSocket push directly to Cytoscape.js & React 18 frontend.",
            "business_impact": "Reduces triage from 45 minutes to <30 seconds; saves $5,600+ per downtime minute."
        },
        {
            "category": "Architecture Drift",
            "challenge": "Outdated architecture diagrams and blind service dependencies",
            "traditional_flaw": "Manual Confluence documentation drifts out of date within days.",
            "logsentinel_solution": "Runtime Dynamic Service Topology Autodiscovery.",
            "algorithmic_mechanism": "Constructs dependency edges on-the-fly from incoming traces and timestamps.",
            "business_impact": "Always-accurate live architecture visibility with zero manual documentation effort."
        },
        {
            "category": "Deployment & Scale",
            "challenge": "Heavy monitoring agents crashing microservice nodes",
            "traditional_flaw": "Java/Go monitoring daemons consume hundreds of megabytes of RAM.",
            "logsentinel_solution": "Lightweight Async Valkey Buffer + Non-blocking Python Workers.",
            "algorithmic_mechanism": "Decoupled O(1) XADD queue buffer; peak RSS memory <52MB.",
            "business_impact": "10,000+ logs/sec ingestion on standard commodity cloud instances without node lag."
        }
    ]

    # -------------------------------------------------------------
    # 3. System Architecture & Tech Stack Data
    # -------------------------------------------------------------
    tech_stack_data = [
        {"layer": "Frontend Core", "tech": "React 18 & Vite 6", "spec": "React 18.3.1 / Vite 6.3.5", "role": "Reactive component tree, sub-second HMR, concurrent UI rendering.", "perf": "60 FPS rendering", "template_ref": "PlayMap Slide 6"},
        {"layer": "Frontend Styling", "tech": "TailwindCSS & Framer Motion", "spec": "TailwindCSS 4.1.12 / Motion 12.42.2", "role": "Dark cybernetic design system, glassmorphism, micro-animations.", "perf": "Zero CSS runtime overhead", "template_ref": "PlayMap Slide 6"},
        {"layer": "Frontend Graph", "tech": "Cytoscape.js & @xyflow/react", "spec": "Cytoscape 3.34.1 / XYFlow 12.9.0", "role": "Interactive dynamic service topology and blast-radius graph visualizer.", "perf": "Hardware accelerated", "template_ref": "PlayMap Slide 6"},
        {"layer": "Backend API", "tech": "FastAPI & Uvicorn", "spec": "FastAPI 0.138.0 / Python 3.11+", "role": "Asynchronous REST endpoints (/v1/logs, /ingest-log) and WebSockets.", "perf": "Non-blocking ASGI loop", "template_ref": "PlayMap Slide 7"},
        {"layer": "Validation", "tech": "Pydantic v2", "spec": "Pydantic 2.13.4", "role": "Strict type validation for LogWindow, FeatureVector, and ParsedLog.", "perf": "Rust-backed sub-ms validation", "template_ref": "PlayMap Slide 7"},
        {"layer": "Ingestion Buffer", "tech": "Valkey 8.0 & Redis Streams", "spec": "Valkey 8.0 / redis-py 8.1.0", "role": "Decoupled memory stream buffer (XADD) eliminating ingestion backpressure.", "perf": "O(1) async write buffer", "template_ref": "PlayMap Slide 7"},
        {"layer": "Template Mining", "tech": "Drain3", "spec": "drain3 0.9.11", "role": "Online prefix-tree log clustering, variable masking [<*>], and compression.", "perf": "<0.5ms per log / 95% ratio", "template_ref": "FlutterForecast Slide 13"},
        {"layer": "Machine Learning", "tech": "scikit-learn Isolation Forest", "spec": "scikit-learn 1.9.0 (Joblib)", "role": "Unsupervised anomaly detection on 12-dimensional sliding window vectors.", "perf": "0.959 ROC-AUC / 0.839 F1", "template_ref": "FlutterForecast Slide 11-13"},
        {"layer": "Causal Graph Engine", "tech": "NetworkX", "spec": "NetworkX 3.6.1", "role": "Directed dependency graph analysis, root-cause localization, blast radius.", "perf": "Sub-10ms graph traversal", "template_ref": "FlutterForecast Slide 12"},
        {"layer": "Time-Series DB", "tech": "TimescaleDB & PostgreSQL", "spec": "TimescaleDB / asyncpg 0.31.0", "role": "Partitioned hypertables for petabyte time-series log persistence and continuous aggregates.", "perf": "Sub-5ms persistent writes", "template_ref": "PlayMap Slide 7"},
        {"layer": "Identity & Security", "tech": "OAuth2 & MSAL", "spec": "Microsoft Entra / Google OAuth", "role": "Enterprise single sign-on, tenant-scoped API keys, and RBAC.", "perf": "Zero-trust token verification", "template_ref": "FlutterForecast Slide 5"},
        {"layer": "Infrastructure", "tech": "Docker Compose & Prometheus", "spec": "Docker Compose / Prom-FastAPI", "role": "Turnkey 1-command fleet deployment and live metrics monitoring.", "perf": "Turnkey deployment", "template_ref": "FlutterForecast Slide 6"}
    ]

    # -------------------------------------------------------------
    # 4. Business Model & Pricing Tiers Data
    # -------------------------------------------------------------
    pricing_tiers_data = [
        {
            "tier": "Community Edition",
            "pricing": "Free ($0) Open Source",
            "target": "Individual developers, open-source projects, and small dev teams.",
            "core_features": "Turnkey Docker Compose fleet, Drain3 parser, baseline Isolation Forest, local WebSocket UI.",
            "limits": "Up to 1,000 logs/sec, single tenant, local storage retention.",
            "support": "GitHub Issues & Community Discord forum."
        },
        {
            "tier": "LogSentinel Pro",
            "pricing": "$49 / node / month",
            "target": "Growth-stage startups, cloud-native scaleups, and SRE teams.",
            "core_features": "High-throughput Valkey stream cluster, Dynamic Causal Graph & Blast Radius, multi-tenancy, automated model retraining, Slack/PagerDuty webhooks.",
            "limits": "Up to 25,000 logs/sec/node, 90-day TimescaleDB retention.",
            "support": "Standard email & ticketing support (8hr response time SLA)."
        },
        {
            "tier": "Enterprise Sentinel",
            "pricing": "Custom (Starts at $1,499 / mo)",
            "target": "Fortune 500 enterprises, FinTech, Healthcare, and mission-critical cloud providers.",
            "core_features": "Everything in Pro plus eBPF kernel tracing, Microsoft Entra ID & Google SSO, Valkey HA cluster, air-gapped / on-prem VPC deployment, custom anomaly model tuning.",
            "limits": "Unlimited logs/sec, custom multi-year cold storage archiving.",
            "support": "24/7 dedicated SRE support channel, 99.99% uptime SLA, dedicated technical account manager."
        },
        {
            "tier": "Cloud Booster: SIEM Bridge",
            "pricing": "$199 / month (Add-on)",
            "target": "Security teams using Splunk or Datadog.",
            "core_features": "Bi-directional forwarding of parsed Drain3 templates and scored anomaly events into existing SIEM dashboards.",
            "limits": "Add-on for Pro or Enterprise.",
            "support": "Included with plan."
        },
        {
            "tier": "Advisory: SRE Chaos Audit",
            "pricing": "$5,000 one-time engagement",
            "target": "Enterprises preparing for major production launches or compliance audits.",
            "core_features": "Full architectural review, custom failure injection scenarios, model calibration, and disaster recovery runbook validation.",
            "limits": "1-week intensive engagement.",
            "support": "Direct access to LogSentinel core architects."
        }
    ]

    # -------------------------------------------------------------
    # 5. Empirical Benchmarks & ML Evaluation Data
    # -------------------------------------------------------------
    benchmarks_data = [
        {"metric": "Ingestion Throughput (Worker Node)", "value": "10,000+ logs/sec", "benchmark_dataset": "Multi-service synthetic load test (AWS c6i.2xlarge)", "baseline": "ELK Stack (~2,500 logs/sec)", "significance": "4x higher throughput with zero backpressure via Valkey streams."},
        {"metric": "End-to-End Latency (p99)", "value": "< 120 ms", "benchmark_dataset": "Collector -> Ingest -> Drain3 -> ML -> WebSocket", "baseline": "Datadog / New Relic (15-60s delay)", "significance": "True real-time alerting before cascading outages propagate."},
        {"metric": "Drain3 Template Compression Ratio", "value": "95%+ Data Reduction", "benchmark_dataset": "5,000 production records across auth, order, payment", "baseline": "Raw JSON indexing (0% compression)", "significance": "Slashes database disk and in-memory indexing costs by 90%+."},
        {"metric": "Drain3 Parsing Speed", "value": "< 0.5 ms per log", "benchmark_dataset": "Online single-pass prefix-tree cluster matching", "baseline": "Regex parsers (5-15ms per log)", "significance": "Sub-millisecond token extraction prevents worker bottlenecking."},
        {"metric": "ML Model ROC-AUC Score", "value": "0.959 ROC-AUC", "benchmark_dataset": "5,000 logs: 4,250 normal + 750 anomalies (seed 42)", "baseline": "Static threshold alerts (0.680 ROC-AUC)", "significance": "Exceptional discriminatory power between normal noise and true incidents."},
        {"metric": "Optimal Decision Threshold", "value": "-0.075", "benchmark_dataset": "Threshold sweep from -0.800 to 0.000 across 84 windows", "baseline": "Default 0.0 threshold (F1: 0.700)", "significance": "Tuning increases F1-score from 0.700 to 0.839 while cutting FPR by 73%."},
        {"metric": "Precision at Optimal Threshold", "value": "81.25%", "benchmark_dataset": "13 True Positives, 3 False Positives", "baseline": "56.00% at default threshold", "significance": "Dramatic reduction in false alarms, directly solving alert fatigue."},
        {"metric": "Recall at Optimal Threshold", "value": "86.67%", "benchmark_dataset": "13 True Positives detected out of 15 anomalous windows", "baseline": "93.33% at cost of high false alarms", "significance": "Maintains high incident capture rate with minimal false positives."},
        {"metric": "Memory Footprint (Peak RSS)", "value": "51.05 MB", "benchmark_dataset": "Stress run at concurrency 50 with 500 records/batch", "baseline": "Java-based agents (500MB - 1GB RSS)", "significance": "Ultra-low memory footprint allows deployment on resource-constrained nodes."},
        {"metric": "Causal Graph Traversal Speed", "value": "< 8 ms", "benchmark_dataset": "NetworkX directed graph with 25 services & 60 edges", "baseline": "Manual SRE investigation (45+ minutes)", "significance": "Sub-second blast radius calculation for instantaneous dashboard updates."}
    ]

    # -------------------------------------------------------------
    # Create Excel Workbook using openpyxl
    # -------------------------------------------------------------
    wb = openpyxl.Workbook()
    # Remove default sheet
    default_sheet = wb.active
    wb.remove(default_sheet)

    # Styles
    navy_dark_fill = PatternFill(start_color="0B192C", end_color="0B192C", fill_type="solid")
    header_fill_blue = PatternFill(start_color="1E3E62", end_color="1E3E62", fill_type="solid")
    header_fill_emerald = PatternFill(start_color="064E3B", end_color="064E3B", fill_type="solid")
    header_fill_purple = PatternFill(start_color="3B0764", end_color="3B0764", fill_type="solid")
    header_fill_amber = PatternFill(start_color="78350F", end_color="78350F", fill_type="solid")
    accent_emerald_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
    accent_blue_fill = PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid")
    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=10, bold=True, color="000000")
    font_regular = Font(name="Calibri", size=10, bold=False, color="1E293B")
    font_notes = Font(name="Calibri", size=9, italic=True, color="475569")
    font_metric = Font(name="Consolas", size=9, bold=True, color="047857")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    # Helper function to style a worksheet
    def style_table(ws, headers, data, header_fill, col_widths, alignments=None):
        ws.views.sheetView[0].showGridLines = True
        # Write headers
        ws.row_dimensions[1].height = 28
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = font_header
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border

        # Write data
        for row_idx, row_dict in enumerate(data, 2):
            is_zebra = (row_idx % 2 == 0)
            row_fill = zebra_fill if is_zebra else white_fill
            ws.row_dimensions[row_idx].height = 65 if len(headers) > 6 else 45
            for col_idx, key in enumerate(row_dict.keys(), 1):
                val = row_dict[key]
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = font_regular
                cell.fill = row_fill
                cell.border = thin_border

                align = "left"
                val_align = "top"
                if alignments and key in alignments:
                    align = alignments[key]
                elif isinstance(val, (int, float)) or (isinstance(val, str) and len(val) < 15 and ("$" in val or "%" in val or "Slide" in val)):
                    align = "center"

                cell.alignment = Alignment(horizontal=align, vertical=val_align, wrap_text=True)

                # Special styling for metrics or notes
                if "metric" in key.lower() or "score" in key.lower() or "value" in key.lower():
                    cell.font = font_metric
                elif "note" in key.lower():
                    cell.font = font_notes

        # Column widths
        for col_idx, width in enumerate(col_widths, 1):
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = width

    # -------------------------------------------------------------
    # Sheet 1: Presentation Slides Deck
    # -------------------------------------------------------------
    ws1 = wb.create_sheet(title="Presentation Slides Deck")
    s1_headers = [
        "Slide #", "Slide Title", "Template Reference", "Slide Section", 
        "Headline / Header", "Visual Layout & Elements", "On-Slide Content", 
        "Key Metrics & Data Points", "Design & Color Palette", "Speaker Notes & Pitch Script"
    ]
    s1_widths = [10, 26, 22, 16, 26, 32, 45, 26, 25, 55]
    s1_alignments = {
        "slide_num": "center",
        "category": "center",
        "template_ref": "center"
    }
    style_table(ws1, s1_headers, slides_data, header_fill_blue, s1_widths, s1_alignments)
    ws1.freeze_panes = "A2"

    # -------------------------------------------------------------
    # Sheet 2: Problem-Solution Fit Matrix
    # -------------------------------------------------------------
    ws2 = wb.create_sheet(title="Problem-Solution Fit Matrix")
    s2_headers = [
        "Problem Category", "Microservice Challenge", "Traditional Monitoring Flaw",
        "LogSentinel Solution", "Algorithmic Mechanism", "Business Impact & ROI"
    ]
    s2_widths = [20, 28, 30, 28, 35, 32]
    style_table(ws2, s2_headers, fit_matrix_data, header_fill_emerald, s2_widths)
    ws2.freeze_panes = "A2"

    # -------------------------------------------------------------
    # Sheet 3: Architecture & Tech Stack
    # -------------------------------------------------------------
    ws3 = wb.create_sheet(title="Tech Stack & Architecture")
    s3_headers = [
        "Architecture Layer", "Technology / Tool", "Version / Spec",
        "Role & Function in LogSentinel", "Performance Characteristic", "Template Reference"
    ]
    s3_widths = [20, 24, 24, 38, 26, 22]
    style_table(ws3, s3_headers, tech_stack_data, header_fill_purple, s3_widths)
    ws3.freeze_panes = "A2"

    # -------------------------------------------------------------
    # Sheet 4: Business Model & Pricing
    # -------------------------------------------------------------
    ws4 = wb.create_sheet(title="Business Model & Tiers")
    s4_headers = [
        "Plan Tier", "Pricing Model", "Target Customer",
        "Core Features Included", "Ingestion & Retention Limits", "Support & SLA"
    ]
    s4_widths = [22, 22, 28, 42, 32, 30]
    style_table(ws4, s4_headers, pricing_tiers_data, header_fill_amber, s4_widths)
    ws4.freeze_panes = "A2"

    # -------------------------------------------------------------
    # Sheet 5: Empirical Benchmarks & ML Eval
    # -------------------------------------------------------------
    ws5 = wb.create_sheet(title="Benchmarks & ML Evaluation")
    s5_headers = [
        "Evaluation Metric", "Observed Value", "Benchmark Dataset / Conditions",
        "Comparison Baseline", "Engineering Significance"
    ]
    s5_widths = [28, 20, 36, 26, 42]
    style_table(ws5, s5_headers, benchmarks_data, navy_dark_fill, s5_widths)
    ws5.freeze_panes = "A2"

    # Save Excel Workbook
    xlsx_path = os.path.join("presentation", "LogSentinel_Presentation_GoogleSheets.xlsx")
    wb.save(xlsx_path)
    print(f"Saved Excel presentation workbook to: {xlsx_path}")

    # -------------------------------------------------------------
    # 6. Generate CSV export for direct Google Sheets upload
    # -------------------------------------------------------------
    csv_path = os.path.join("presentation", "LogSentinel_Presentation.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(s1_headers)
        for s in slides_data:
            writer.writerow([
                s["slide_num"],
                s["title"],
                s["template_ref"],
                s["category"],
                s["headline"],
                s["visual_layout"],
                s["main_content"],
                s["key_metrics"],
                s["design_style"],
                s["speaker_notes"]
            ])
    print(f"Saved CSV presentation export to: {csv_path}")

    # -------------------------------------------------------------
    # 7. Generate Google Apps Script (.gs) to auto-build Google Slides
    # -------------------------------------------------------------
    gs_path = os.path.join("presentation", "generate_google_slides.gs")
    slides_json = json.dumps(slides_data, indent=2)
    
    gs_code = f"""/**
 * Google Apps Script: Auto-Generate LogSentinel Google Slides Presentation
 * 
 * Instructions:
 * 1. Open Google Drive (drive.google.com) or Google Sheets.
 * 2. In Google Sheets (or standalone at script.google.com), open Extensions -> Apps Script.
 * 3. Paste this code into Code.gs.
 * 4. Click 'Run' -> execute generateLogSentinelPresentation().
 * 5. Grant permissions when prompted.
 * 6. The script creates a complete 18-slide Google Slides presentation in your Google Drive!
 */

function generateLogSentinelPresentation() {{
  var deckTitle = "LogSentinel - Technical Pitch Deck & Architecture";
  var presentation = SlidesApp.create(deckTitle);
  var slidesData = {slides_json};

  // Color Palette Constants
  var COLOR_DARK_BG = '#0B0F19';
  var COLOR_CARD_BG = '#161F30';
  var COLOR_EMERALD = '#10B981';
  var COLOR_CYAN = '#06B6D4';
  var COLOR_BLUE = '#3B82F6';
  var COLOR_ORANGE = '#F97316';
  var COLOR_TEXT_WHITE = '#FFFFFF';
  var COLOR_TEXT_MUTED = '#94A3B8';

  // Remove initial blank slide
  var initialSlides = presentation.getSlides();
  if (initialSlides.length > 0) {{
    initialSlides[0].remove();
  }}

  for (var i = 0; i < slidesData.length; i++) {{
    var data = slidesData[i];
    var slide = presentation.appendSlide(SlidesApp.PredefinedLayout.BLANK);
    
    // Set Background Color
    slide.getBackground().setSolidFill(COLOR_DARK_BG);

    // Slide Header: Section Category Badge
    var catBox = slide.insertTextBox(data.category.toUpperCase(), 40, 25, 250, 25);
    catBox.getText().getTextStyle()
      .setForegroundColor(COLOR_EMERALD)
      .setFontSize(11)
      .setBold(true)
      .setFontFamily('Consolas');

    // Slide Title
    var titleBox = slide.insertTextBox(data.title, 40, 50, 640, 45);
    titleBox.getText().getTextStyle()
      .setForegroundColor(COLOR_TEXT_WHITE)
      .setFontSize(20)
      .setBold(true)
      .setFontFamily('Trebuchet MS');

    // Slide Sub-Headline
    var subBox = slide.insertTextBox(data.headline, 40, 95, 640, 25);
    subBox.getText().getTextStyle()
      .setForegroundColor(COLOR_CYAN)
      .setFontSize(13)
      .setItalic(true)
      .setFontFamily('Trebuchet MS');

    // Main Content Card Background Shape
    var card = slide.insertShape(SlidesApp.ShapeType.ROUNDED_RECTANGLE, 40, 130, 640, 210);
    card.getFill().setSolidFill(COLOR_CARD_BG);
    card.getBorder().getLineFill().setSolidFill(COLOR_BLUE);
    card.getBorder().setWeight(1);

    // Main Content Text inside Card
    var contentBox = slide.insertTextBox(data.main_content, 55, 140, 610, 190);
    contentBox.getText().getTextStyle()
      .setForegroundColor(COLOR_TEXT_WHITE)
      .setFontSize(10)
      .setFontFamily('Calibri');

    // Key Metrics Banner at bottom
    var metricCard = slide.insertShape(SlidesApp.ShapeType.ROUNDED_RECTANGLE, 40, 350, 640, 35);
    metricCard.getFill().setSolidFill('#0F172A');
    metricCard.getBorder().getLineFill().setSolidFill(COLOR_EMERALD);
    metricCard.getBorder().setWeight(1);

    var metricBox = slide.insertTextBox("KEY METRICS: " + data.key_metrics, 55, 355, 610, 25);
    metricBox.getText().getTextStyle()
      .setForegroundColor(COLOR_EMERALD)
      .setFontSize(10)
      .setBold(true)
      .setFontFamily('Consolas');

    // Speaker Notes
    var notes = slide.getNotesPage();
    var speakerNotesText = "SLIDE " + data.slide_num + " - " + data.title + "\\n\\n" +
      "TEMPLATE REFERENCE: " + data.template_ref + "\\n\\n" +
      "SPEAKER NOTES & TALKING POINTS:\\n" + data.speaker_notes;
    notes.getSpeakerNotesShape().getText().setText(speakerNotesText);
  }}

  Logger.log("Successfully generated presentation: " + presentation.getUrl());
  return presentation.getUrl();
}}
"""
    with open(gs_path, "w", encoding="utf-8") as f:
        f.write(gs_code)
    print(f"Saved Google Apps Script to: {gs_path}")

    # -------------------------------------------------------------
    # 8. Generate interactive presentation preview HTML
    # -------------------------------------------------------------
    html_path = os.path.join("presentation", "presentation_preview.html")
    slides_json_escaped = json.dumps(slides_data)
    fit_matrix_json = json.dumps(fit_matrix_data)
    tech_stack_json = json.dumps(tech_stack_data)
    benchmarks_json = json.dumps(benchmarks_data)

    html_code = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>LogSentinel Presentation Deck Preview</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg-dark: #070B14;
      --card-bg: #0F172A;
      --card-border: #1E293B;
      --accent-emerald: #10B981;
      --accent-cyan: #06B6D4;
      --accent-blue: #3B82F6;
      --accent-orange: #F97316;
      --accent-purple: #8B5CF6;
      --text-main: #F8FAFC;
      --text-muted: #94A3B8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', sans-serif;
      background: var(--bg-dark);
      color: var(--text-main);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}
    header {{
      background: rgba(15, 23, 42, 0.8);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--card-border);
      padding: 16px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
    }}
    .logo-container {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .logo-badge {{
      background: linear-gradient(135deg, var(--accent-emerald), var(--accent-cyan));
      width: 38px;
      height: 38px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: 'Outfit', sans-serif;
      font-weight: 800;
      color: #070B14;
      font-size: 20px;
    }}
    .logo-text h1 {{
      font-family: 'Outfit', sans-serif;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }}
    .logo-text p {{
      font-size: 11px;
      color: var(--text-muted);
    }}
    .header-actions {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .btn {{
      padding: 8px 16px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
      transition: all 0.2s;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .btn-primary {{
      background: var(--accent-emerald);
      color: #070B14;
      border: none;
    }}
    .btn-primary:hover {{ background: #059669; }}
    .btn-outline {{
      background: transparent;
      color: var(--text-main);
      border: 1px solid var(--card-border);
    }}
    .btn-outline:hover {{ background: rgba(255,255,255,0.05); }}
    
    .deck-container {{
      flex: 1;
      display: flex;
      max-width: 1400px;
      margin: 0 auto;
      width: 100%;
      padding: 24px;
      gap: 24px;
    }}
    .slide-sidebar {{
      width: 320px;
      max-height: calc(100vh - 120px);
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding-right: 8px;
    }}
    .slide-nav-item {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 12px 14px;
      cursor: pointer;
      transition: all 0.15s ease;
      display: flex;
      align-items: flex-start;
      gap: 10px;
    }}
    .slide-nav-item:hover {{
      border-color: var(--accent-cyan);
      transform: translateX(3px);
    }}
    .slide-nav-item.active {{
      border-color: var(--accent-emerald);
      background: #132238;
      box-shadow: 0 0 15px rgba(16, 185, 129, 0.15);
    }}
    .nav-num {{
      background: rgba(255,255,255,0.06);
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      font-weight: 600;
      padding: 3px 6px;
      border-radius: 4px;
      color: var(--accent-cyan);
    }}
    .nav-info h4 {{
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 3px;
      line-height: 1.3;
    }}
    .nav-info span {{
      font-size: 10px;
      color: var(--text-muted);
    }}

    .slide-stage {{
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .slide-canvas {{
      background: #0B1120;
      border: 1px solid #1E293B;
      border-radius: 16px;
      aspect-ratio: 16 / 9;
      position: relative;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      padding: 48px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
    }}
    .slide-watermark {{
      position: absolute;
      top: 24px;
      right: 32px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: rgba(255,255,255,0.25);
      background: rgba(255,255,255,0.05);
      padding: 4px 10px;
      border-radius: 6px;
    }}
    .slide-badge {{
      display: inline-block;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      color: var(--accent-emerald);
      text-transform: uppercase;
      letter-spacing: 1.5px;
      margin-bottom: 8px;
    }}
    .slide-title {{
      font-family: 'Outfit', sans-serif;
      font-size: 32px;
      font-weight: 700;
      margin-bottom: 6px;
      line-height: 1.2;
    }}
    .slide-headline {{
      font-size: 16px;
      color: var(--accent-cyan);
      margin-bottom: 24px;
      font-weight: 500;
    }}
    .slide-card-container {{
      flex: 1;
      background: rgba(15, 23, 42, 0.75);
      border: 1px solid rgba(59, 130, 246, 0.3);
      border-radius: 12px;
      padding: 24px 28px;
      overflow-y: auto;
      white-space: pre-wrap;
      font-size: 14px;
      line-height: 1.6;
      color: #E2E8F0;
    }}
    .slide-footer-metric {{
      margin-top: 16px;
      background: rgba(16, 185, 129, 0.08);
      border: 1px solid rgba(16, 185, 129, 0.3);
      border-radius: 8px;
      padding: 10px 18px;
      display: flex;
      align-items: center;
      gap: 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--accent-emerald);
      font-weight: 600;
    }}

    .notes-panel {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 18px 24px;
    }}
    .notes-header {{
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--accent-orange);
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .notes-text {{
      font-size: 13px;
      line-height: 1.6;
      color: var(--text-muted);
    }}

    .controls-bar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 8px;
    }}
  </style>
</head>
<body>
  <header>
    <div class="logo-container">
      <div class="logo-badge">LS</div>
      <div class="logo-text">
        <h1>LogSentinel Presentation Deck</h1>
        <p>Generated from PlayMap & FlutterForecast Pitch Deck Templates</p>
      </div>
    </div>
    <div class="header-actions">
      <span style="font-size: 12px; color: var(--text-muted); margin-right: 8px;">Use ← / → keys to navigate</span>
      <a href="./LogSentinel_Presentation_GoogleSheets.xlsx" download class="btn btn-primary">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
        Download Excel (.xlsx)
      </a>
      <a href="./LogSentinel_Presentation.csv" download class="btn btn-outline">
        Download CSV
      </a>
    </div>
  </header>

  <div class="deck-container">
    <div class="slide-sidebar" id="sidebar"></div>
    <div class="slide-stage">
      <div class="slide-canvas">
        <div class="slide-watermark" id="slideWatermark">Slide 1 of 18</div>
        <div class="slide-badge" id="slideBadge">TITLE & VISION</div>
        <div class="slide-title" id="slideTitle">LogSentinel</div>
        <div class="slide-headline" id="slideHeadline">Taming Microservice Chaos in Real-Time</div>
        <div class="slide-card-container" id="slideContent"></div>
        <div class="slide-footer-metric" id="slideMetric">
          <span>⚡ KEY METRIC:</span>
          <span id="metricText">10,000+ logs/sec | &lt;120ms Latency</span>
        </div>
      </div>
      <div class="controls-bar">
        <button class="btn btn-outline" id="prevBtn" onclick="prevSlide()">← Previous Slide</button>
        <span id="slideIndicator" style="font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--text-muted);">1 / 18</span>
        <button class="btn btn-primary" id="nextBtn" onclick="nextSlide()">Next Slide →</button>
      </div>
      <div class="notes-panel">
        <div class="notes-header">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"/></svg>
          Speaker Notes & Pitch Script
        </div>
        <div class="notes-text" id="speakerNotes"></div>
      </div>
    </div>
  </div>

  <script>
    const slides = {slides_json_escaped};
    let currentIndex = 0;

    function renderSidebar() {{
      const sidebar = document.getElementById('sidebar');
      sidebar.innerHTML = '';
      slides.forEach((s, idx) => {{
        const item = document.createElement('div');
        item.className = 'slide-nav-item' + (idx === currentIndex ? ' active' : '');
        item.onclick = () => goToSlide(idx);
        item.innerHTML = `
          <div class="nav-num">${{String(s.slide_num).padStart(2, '0')}}</div>
          <div class="nav-info">
            <h4>${{s.title}}</h4>
            <span>${{s.category}}</span>
          </div>
        `;
        sidebar.appendChild(item);
      }});
    }}

    function updateSlide() {{
      const s = slides[currentIndex];
      document.getElementById('slideWatermark').innerText = `Slide ${{s.slide_num}} of ${{slides.length}} • ${{s.template_ref}}`;
      document.getElementById('slideBadge').innerText = s.category;
      document.getElementById('slideTitle').innerText = s.title;
      document.getElementById('slideHeadline').innerText = s.headline;
      document.getElementById('slideContent').innerText = s.main_content;
      document.getElementById('metricText').innerText = s.key_metrics;
      document.getElementById('speakerNotes').innerText = s.speaker_notes;
      document.getElementById('slideIndicator').innerText = `${{currentIndex + 1}} / ${{slides.length}}`;

      renderSidebar();
    }}

    function goToSlide(idx) {{
      currentIndex = idx;
      updateSlide();
    }}

    function prevSlide() {{
      if (currentIndex > 0) {{
        currentIndex--;
        updateSlide();
      }}
    }}

    function nextSlide() {{
      if (currentIndex < slides.length - 1) {{
        currentIndex++;
        updateSlide();
      }}
    }}

    document.addEventListener('keydown', (e) => {{
      if (e.key === 'ArrowRight' || e.key === ' ') {{
        nextSlide();
      }} else if (e.key === 'ArrowLeft') {{
        prevSlide();
      }}
    }});

    updateSlide();
  </script>
</body>
</html>
"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_code)
    print(f"Saved HTML presentation preview to: {html_path}")

    # -------------------------------------------------------------
    # 9. Generate presentation README.md with import guide
    # -------------------------------------------------------------
    readme_path = os.path.join("presentation", "README.md")
    readme_md = """# LogSentinel Technical Pitch Deck & Presentation Export

Based on the provided presentation deck templates (**PlayMap** and **FlutterForecast**), this directory contains the complete presentation materials for **LogSentinel**.

## 📦 Generated Deliverables

| File | Format | Description |
|---|---|---|
| **[`LogSentinel_Presentation_GoogleSheets.xlsx`](./LogSentinel_Presentation_GoogleSheets.xlsx)** | Excel / Google Sheets | **Primary Export**: Multi-tab workbook with 18 slides, Problem-Solution Fit Matrix, Tech Stack breakdown, Business Model tiers, and Empirical Benchmarks. |
| **[`LogSentinel_Presentation.csv`](./LogSentinel_Presentation.csv)** | CSV | Standard CSV table ready for `File -> Import` into Google Sheets with 1 click. |
| **[`generate_google_slides.gs`](./generate_google_slides.gs)** | Google Apps Script | Auto-generator script that creates the complete 18-slide Google Slides presentation in your Google Drive. |
| **[`presentation_preview.html`](./presentation_preview.html)** | Interactive Web Deck | Visual presentation preview with keyboard navigation (← / →), design styling, and full speaker notes. |

---

## 🚀 How to Open in Google Sheets

### Method A: Open the Excel Workbook Directly in Google Drive (Recommended)
1. Go to [Google Drive](https://drive.google.com).
2. Click **New (+)** -> **File upload**.
3. Select `LogSentinel_Presentation_GoogleSheets.xlsx`.
4. Double-click the uploaded file. Google Drive will automatically open it as a full-fidelity **Google Sheet** with all 5 tabs and formatting preserved!

### Method B: Import via File Menu in an Existing Google Sheet
1. Open a blank sheet at [sheets.new](https://sheets.new).
2. Click **File** -> **Import** -> **Upload**.
3. Select `LogSentinel_Presentation.csv` or `LogSentinel_Presentation_GoogleSheets.xlsx`.
4. Choose **"Replace current sheet"** and click **Import data**.

---

## 🎨 How to 1-Click Convert to Google Slides

You can convert this deck into a styled **Google Slides** presentation automatically:

1. Open your Google Sheet (from Method A or B).
2. Click **Extensions** -> **Apps Script** in the top menu bar.
3. Delete any default code in `Code.gs` and paste the contents of **[`generate_google_slides.gs`](./generate_google_slides.gs)**.
4. Click the **Save (💾)** icon, then select `generateLogSentinelPresentation` and click **Run (▶)**.
5. Grant Google permissions when prompted.
6. The script will output the link to your new **Google Slides** presentation with dark cybersecurity styling, structured card layouts, key metrics, and speaker notes attached to every slide!

---

## 📋 Slide Deck Structure (18 Slides)

1. **Slide 1: Title & Vision** (LogSentinel: Real-Time Observability & Blast-Radius Engine)
2. **Slide 2: The Engineering Team** (5 Core Engineers & Responsibilities)
3. **Slide 3: The Problem** (3 Numbered Cards: Microservice Chaos, Alert Fatigue, Downtime Cost)
4. **Slide 4: Data-Driven Research & Benchmarks** (82% Cascade Outages, 67% MTTR Wasted, 10k logs/sec)
5. **Slide 5: The LogSentinel Solution** (3 Numbered Pillars: Streaming Ingest, Unsupervised ML, Causal Graph)
6. **Slide 6: Engineering Lifecycle & Phases** (5 Milestone Steps: Research -> Ingest -> ML -> Full-Stack -> Chaos QA)
7. **Slide 7: Technologies: Front-End** (3 Columns: React 18, TailwindCSS/Motion, Cytoscape.js/@xyflow)
8. **Slide 8: Technologies: Back-End & Data Pipeline** (3 Columns: FastAPI, Valkey 8.0, TimescaleDB)
9. **Slide 9: End-to-End System Architecture** (Collectors -> Valkey -> Drain3 -> ML -> Causal Graph -> WebSockets)
10. **Slide 10: Algorithmic Core** (3 Components: Drain3 Parser, 12-Feature Window, Isolation Forest)
11. **Slide 11: Dynamic Causal Graph & Blast Radius** (Topology Autodiscovery, Root-Cause Ranking, Noise Suppression)
12. **Slide 12: Problem-Solution Fit Matrix** (3 Direct Mappings with ROI Impact)
13. **Slide 13: Live Demonstration & Chaos Incident** (4-Step Chaos Injection & Live UI Reaction)
14. **Slide 14: Added Value & ROI Breakdown** (5-Way 20% Value Wheel: MTTR, Alert Noise, Zero-Rule ML, Scale, Topology)
15. **Slide 15: Business Model: Tiered Platform Pricing** (Community Free, Pro $49/node, Enterprise $1,499+)
16. **Slide 16: Business Model: Add-ons & Professional Services** (Cloud Boosters, SRE Chaos Audits)
17. **Slide 17: Future Roadmap & Innovation Horizon** (eBPF Kernel Tracing, GenAI Post-Mortems, K8s Auto-Remediation)
18. **Slide 18: Thank You & Open Q&A** (Closing Banner, Team Contacts, GitHub & Docs)
"""
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_md)
    print(f"Saved presentation README to: {readme_path}")

if __name__ == "__main__":
    build_presentation()

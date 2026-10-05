/**
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

function generateLogSentinelPresentation() {
  var deckTitle = "LogSentinel - Technical Pitch Deck & Architecture";
  var presentation = SlidesApp.create(deckTitle);
  var slidesData = [
  {
    "slide_num": 1,
    "title": "LogSentinel: Real-Time Observability & Blast-Radius Engine",
    "template_ref": "PlayMap Slide 1 / FlutterForecast Slide 1",
    "category": "Title & Vision",
    "headline": "Taming Microservice Chaos in Real Time",
    "visual_layout": "Cover Slide: Centered Logo emblem, Bold Title, Subtitle, Pill-shaped Slogan tag ('your journey starts here' style), Cyber-navy background with emerald/cyan neon accents.",
    "main_content": "LOGSENTINEL\nReal-Time Unsupervised Log Anomaly Detection, Dynamic Topology Mapping & Root-Cause Blast-Radius Ranking\n\n\u2022 Autonomous Real-Time Stream Ingestion (10,000+ logs/sec)\n\u2022 Unsupervised ML Anomaly Detection (Drain3 + Isolation Forest)\n\u2022 Dynamic Microservice Causal Graph & Blast-Radius Ranking\n\u2022 High-Performance Full-Stack Fleet (FastAPI + Valkey + TimescaleDB + React 18)",
    "key_metrics": "10,000+ logs/sec | <120ms E2E Latency | 95%+ Compression Ratio",
    "design_style": "Background: #0B0F19 (Dark Cyber), Primary: #10B981 (Emerald), Secondary: #3B82F6 (Blue), Accent: #F97316 (Orange)",
    "speaker_notes": "Good morning everyone. Today we are excited to introduce LogSentinel\u2014an intelligent, high-throughput observability platform engineered to solve one of modern software engineering's biggest headaches: microservice chaos. In complex distributed clouds, when one component fails, everything seems to catch fire. LogSentinel ingests millions of logs in real time, mines semantic templates without human intervention, catches unseen anomalies with unsupervised machine learning, and maps the exact cascading blast radius so engineers know the true root cause within seconds."
  },
  {
    "slide_num": 2,
    "title": "The Engineering Team",
    "template_ref": "PlayMap Slide 2 / FlutterForecast Slide 2",
    "category": "Team",
    "headline": "Meet the Minds Behind LogSentinel",
    "visual_layout": "5 Circular Profile Avatars horizontally aligned with Role Badges, Expertise, and Core Responsibilities.",
    "main_content": "1. Melos Hajrullahu \u2014 Team Leader & ML Systems Architect\n   \u2022 Unsupervised Isolation Forest pipeline, threshold optimization, and algorithmic evaluation.\n\n2. Leorent Ismajli \u2014 Core Engine & Distributed Streaming Lead\n   \u2022 Valkey stream buffer, Drain3 online template miner, and async ingestion queue.\n\n3. Blerim Haxhiu \u2014 Frontend & Dynamic Graph Visualization Engineer\n   \u2022 React 18, Vite, Cytoscape.js & @xyflow/react dynamic service topology dashboard.\n\n4. Blert Sylejmani \u2014 Backend API & Time-Series Database Specialist\n   \u2022 FastAPI endpoints, TimescaleDB hypertables, WebSocket telemetry broadcast.\n\n5. Juled Morina \u2014 SRE, Chaos Engineering & Production Hardening\n   \u2022 Synthetic chaos injection, Docker Compose fleet orchestration, and benchmark validation.",
    "key_metrics": "5 Core Engineers | 100+ Tests Passing | Multi-Tenant Architecture",
    "design_style": "5 Circular portrait frames with gold/emerald ring borders, role pill badges, clean typography.",
    "speaker_notes": "Our team brings together distributed systems engineering, streaming data infrastructure, machine learning, and modern UI visualization. Melos spearheaded our ML pipeline and statistical threshold sweeps; Leorent architected our ultra-fast Valkey and Drain3 log ingestion pipeline; Blerim built our real-time interactive React 18 topology graph; Blert optimized the TimescaleDB hypertables and FastAPI telemetry; and Juled designed our chaos engineering and capacity benchmarks."
  },
  {
    "slide_num": 3,
    "title": "The Problem: Microservice Chaos & Observability Blindspots",
    "template_ref": "PlayMap Slide 3 (3 Numbered Cards) & FlutterForecast Slide 3",
    "category": "Problem",
    "headline": "Why Modern Distributed Cloud Observability Fails",
    "visual_layout": "3 Numbered Rectangular Cards (1, 2, 3) with bold accent headers, icons, and symptom descriptions.",
    "main_content": "[Card 1] Microservice Complexity & Cascading Outages\n\u2022 Cloud systems consist of dozens of decoupled services. When a database or auth service stalls, failures cascade down the dependency chain.\n\u2022 Unstructured logs explode into millions of noisy lines, obscuring the primary fault.\n\n[Card 2] Alert Fatigue & False Alarms\n\u2022 SREs are inundated with hundreds of disjointed alerts from downstream victims (e.g. 504 timeouts in UI, payment retries, queue backlogs).\n\u2022 Over 70% of alerts during an incident are symptoms, not root causes.\n\n[Card 3] High MTTR & Staggering Downtime Cost\n\u2022 Manual log grep and correlation takes 45 to 90 minutes on average.\n\u2022 Enterprise downtime costs average $5,600+ per minute ($300,000+ per hour), inflicting severe SLA penalties and brand damage.",
    "key_metrics": "$5,600/min Downtime Cost | 70%+ Symptom Noise | 45-90 min Average MTTR",
    "design_style": "3 Colored Cards: #F97316 (Orange - Disruption), #EF4444 (Red - Alert Fatigue), #6366F1 (Indigo - MTTR Cost)",
    "speaker_notes": "Let's look at the reality of modern cloud engineering. As monolithic applications were broken into microservices, observability became a nightmare. In a cascade failure, dozens of services light up red simultaneously. SREs suffer from severe alert fatigue. Because teams spend an hour just figuring out WHICH service died first, downtime costs spiral out of control. We built LogSentinel to automate this entire triage loop."
  },
  {
    "slide_num": 4,
    "title": "Data-Driven Research & Industry Benchmarks",
    "template_ref": "PlayMap Slide 4 / FlutterForecast Slide 3",
    "category": "Research & Data",
    "headline": "Empirical Evidence & Operational Scale Challenges",
    "visual_layout": "Boxed statistical card layout featuring 4 oversized metric callouts and verified empirical benchmark results.",
    "main_content": "INDUSTRY RESEARCH DATA:\n\u2022 82% of cloud production outages are triggered by complex interconnected microservice cascades.\n\u2022 67% of Mean Time to Resolution (MTTR) is consumed merely finding the root-cause service.\n\u2022 52% of on-call engineers report burnout from alert fatigue and noisy false positives.\n\nLOGSENTINEL EMPIRICAL VALIDATION:\n\u2022 10,000+ logs/sec sustained ingestion per single worker node without backpressure.\n\u2022 95%+ log compression achieved online by Drain3 template discovery.\n\u2022 0.959 ROC-AUC and 0.839 F1-score in detecting zero-day anomalies across 5,000 production incident records.",
    "key_metrics": "82% Cascading Outages | 67% MTTR Wasted Locating Fault | 0.959 ROC-AUC Score",
    "design_style": "Large data callout stat blocks with glowing green borders on dark background.",
    "speaker_notes": "Our design is grounded in rigorous research. Studies show 82% of outages are cascades and two-thirds of triage time is wasted searching for the root-cause service. We rigorously benchmarked LogSentinel on real failure patterns\u2014including SQL injection, thread starvation, latency spikes, and brute force attacks. Our Drain3 miner compresses logs by over 95%, while our tuned Isolation Forest delivers a 0.959 ROC-AUC and an 83.9% F1-score."
  },
  {
    "slide_num": 5,
    "title": "The LogSentinel Solution: Autonomous Triad",
    "template_ref": "PlayMap Slide 5 (3 Numbered Solution Cards) & FlutterForecast Slide 4",
    "category": "Solution",
    "headline": "Stream Processing + Unsupervised ML + Causal Graph Theory",
    "visual_layout": "3 Numbered Pillar Cards (1, 2, 3) showing the three architectural breakthroughs of LogSentinel.",
    "main_content": "[Pillar 1] Real-Time Streaming Ingestion & High-Density Compression\n\u2022 Ingests standard OTLP, Fluent Bit, and Vector logs via async Valkey stream buffer.\n\u2022 Drain3 tree-based online template mining compresses raw strings into parameterized clusters in sub-milliseconds.\n\n[Pillar 2] Unsupervised ML Anomaly Detection\n\u2022 Sliding-window feature vectors (volume, template entropy, error ratios, burst velocity).\n\u2022 Unsupervised Isolation Forest catches unknown zero-day anomalies without training labels or manual regex rules.\n\n[Pillar 3] Dynamic Causal Topology & Blast-Radius Ranking\n\u2022 Reconstructs service dependency topology dynamically using NetworkX directed causal graphs.\n\u2022 Isolates the upstream root cause and calculates quantitative blast-radius impact within <120ms.",
    "key_metrics": "Zero Manual Rules | O(1) Memory Ingest Buffer | Sub-120ms Incident Broadcast",
    "design_style": "3 Modern Cards with numbered badges: 1 Teal (#0D9488), 2 Blue (#2563EB), 3 Purple (#7C3AED)",
    "speaker_notes": "LogSentinel solves this crisis with a unique 3-pillar architecture. Pillar 1: We ingest up to 10,000+ logs/second into a high-speed memory buffer and compress them into structured templates online. Pillar 2: We use an unsupervised Isolation Forest running across sliding windows to detect anomalies\u2014meaning zero manual regex maintenance. Pillar 3: We feed anomalous windows into a dynamic causal graph that immediately flags the root-cause service and ranks its blast radius."
  },
  {
    "slide_num": 6,
    "title": "Engineering Lifecycle & Solution Phases",
    "template_ref": "FlutterForecast Slide 6 (5 Milestone Step Circles)",
    "category": "Lifecycle / Roadmap",
    "headline": "From Algorithmic Theory to Production Hardening",
    "visual_layout": "5 Connected Horizontal Milestone Circles (1 -> 2 -> 3 -> 4 -> 5) with progress connector line.",
    "main_content": "Phase 01: Intensive Research & Algorithmic Benchmarking\n\u2022 Comparative evaluation of unsupervised models (Isolation Forest vs LOF vs Autoencoders).\n\u2022 Parameter tuning for Drain3 depth and similarity threshold.\n\nPhase 02: Data Gathering & Protocol Ingestion\n\u2022 Native OTLP (/v1/logs), Fluent Bit HTTP sink, Vector sink, and Python SDK.\n\u2022 Tenant-scoped API key authorization and rate limiting.\n\nPhase 03: ML & Causal Engine Development\n\u2022 60s sliding window with 30s stride generating 12-dimensional feature vectors.\n\u2022 NetworkX directed graph propagation for blast-radius scoring.\n\nPhase 04: Full-Stack Platform Development\n\u2022 Asynchronous FastAPI backend + TimescaleDB partitioned hypertables.\n\u2022 React 18, Vite, Cytoscape.js & @xyflow/react live WebSocket dashboard.\n\nPhase 05: Chaos Testing & Production Hardening\n\u2022 Automated chaos injection suite (latency spikes, DB pool exhaustion, SQLi).\n\u2022 Concurrency stress benchmarks, CI/CD linting, and Docker Compose fleet.",
    "key_metrics": "5 End-to-End Milestones | 100% Turnkey Deployment via Docker Compose",
    "design_style": "Horizontal progress track with glowing circular milestone nodes (Emerald, Cyan, Blue, Violet, Amber).",
    "speaker_notes": "Just like FlutterForecast's structured phases, LogSentinel progressed through 5 disciplined engineering milestones: First, algorithmic research on Drain3 and Isolation Forests. Second, universal protocol ingestion supporting OTLP and Fluent Bit. Third, developing our sliding-window feature extraction and causal graph scoring. Fourth, building the reactive full-stack platform with FastAPI and React 18. And fifth, stress-testing under simulated chaos incidents."
  },
  {
    "slide_num": 7,
    "title": "Technologies: Front-End Architecture",
    "template_ref": "PlayMap Slide 6 (3 Vertical Color Columns)",
    "category": "Technology Stack",
    "headline": "Modern, Reactive, High-Performance Visualization",
    "visual_layout": "3 Vertical Color Columns (Column 1: Light Cream/White, Column 2: Amber/Gold, Column 3: Sky Blue).",
    "main_content": "[Column 1] React 18 & Vite 6\n\u2022 Component-driven UI architecture with concurrent rendering.\n\u2022 Ultra-fast Vite HMR build system with sub-second feedback.\n\u2022 TypeScript strict type safety across all telemetry models.\n\n[Column 2] TailwindCSS & Motion\n\u2022 Cyberpunk-sleek dark mode design system (#0B0F19 background).\n\u2022 Responsive glassmorphism with high-contrast status badges.\n\u2022 Micro-animations for live anomaly alerts and pulsing nodes.\n\n[Column 3] Cytoscape.js & @xyflow/react\n\u2022 Dynamic topological graph rendering for distributed microservices.\n\u2022 Real-time node coloring (Green=Healthy, Amber=Degraded, Red=Root Cause).\n\u2022 Interactive blast-radius click-through and dependency path tracing.",
    "key_metrics": "60 FPS Graph Rendering | Sub-second Live WebSocket Updates | Fully Responsive",
    "design_style": "3 Vertical columns with bold headers, technology badges, and capability lists.",
    "speaker_notes": "Looking at our frontend stack: We chose React 18 with Vite for maximum rendering performance and instantaneous HMR. TailwindCSS and Framer Motion provide an intuitive, high-contrast dark mode interface with micro-animations. For the topology visualizer, we integrated Cytoscape.js and XYFlow, allowing SREs to explore dynamic dependency trees rendered at 60 frames per second with instant visual indicators during an incident."
  },
  {
    "slide_num": 8,
    "title": "Technologies: Back-End & Data Pipeline",
    "template_ref": "PlayMap Slide 7 (3 Vertical Color Columns)",
    "category": "Technology Stack",
    "headline": "High-Throughput, Low-Latency Asynchronous Core",
    "visual_layout": "3 Vertical Color Columns (Column 1: Rose/Salmon, Column 2: Amber/Orange, Column 3: Electric Blue).",
    "main_content": "[Column 1] Python 3.11+ & FastAPI\n\u2022 Asynchronous ASGI event loop handling high-concurrency ingestion.\n\u2022 Strict Pydantic v2 data validation schemas.\n\u2022 Native WebSocket server (/ws/telemetry) for real-time pushing.\n\n[Column 2] Valkey 8.0 & Redis Streams\n\u2022 High-throughput XADD ingestion buffer ensuring zero HTTP blocking.\n\u2022 Decoupled asynchronous worker consumer groups.\n\u2022 In-memory caching for tenant rate-limiting and session validation.\n\n[Column 3] TimescaleDB & PostgreSQL\n\u2022 Hypertables optimized for petabyte-scale time-series log persistence.\n\u2022 Continuous aggregates for real-time service health analytics.\n\u2022 Strict tenant isolation via composite foreign-key database constraints.",
    "key_metrics": "O(1) Valkey Ingestion | Sub-5ms Hypertable Writes | Zero-Copy Async Streaming",
    "design_style": "3 Vertical columns with bold back-end logos, architecture tags, and performance specs.",
    "speaker_notes": "On the backend, Python 3.11 and FastAPI power our asynchronous ingestion gateway. We decouple ingestion from processing using Valkey 8.0 streams\u2014giving us zero backpressure even during log spikes. For persistence, TimescaleDB hypertables store billions of time-series log records with continuous aggregates, allowing rapid analytical lookups without degrading operational performance."
  },
  {
    "slide_num": 9,
    "title": "End-to-End System Architecture & Streaming Pipeline",
    "template_ref": "FlutterForecast Slide 11 & README Mermaid Sequence",
    "category": "Architecture",
    "headline": "How Logs Flow from Collectors to Root-Cause Pinpointing",
    "visual_layout": "Pipelined Flow Diagram: 7 Interconnected Stages with arrows, queue buffers, and latency tags.",
    "main_content": "1. COLLECTORS: Fluent Bit, Vector, OpenTelemetry (OTLP), and Python SDK push logs to /v1/logs & /ingest-log.\n2. ASYNC INGESTION: FastAPI validates API key and immediately writes to Valkey stream (XADD) in <2ms.\n3. DRAIN3 MINING: Background worker streams logs through Drain3 tree miner, discovering templates and masking variables [<*>].\n4. WINDOW EXTRACTION: 60s sliding window (30s stride) computes 12 features (entropy, error ratios, rate velocity).\n5. ISOLATION FOREST: Pre-trained scikit-learn model evaluates window vector against optimal threshold (-0.075).\n6. CAUSAL GRAPH: NetworkX engine traverses topological dependencies, computing blast radius and ranking root cause.\n7. WEBSOCKET TELEMETRY: Broadcasts incident alerts, parsed logs, and topology updates to React dashboard (<120ms p99).",
    "key_metrics": "<120ms End-to-End Latency | 7 Processing Stages | Completely Non-Blocking",
    "design_style": "Flow diagram with sequential numbered pill nodes, pipeline arrows, and glowing status tags.",
    "speaker_notes": "Here is how data flows through LogSentinel end-to-end: Logs arrive via OTLP or Fluent Bit into FastAPI. Instead of hitting the database immediately, they land in an in-memory Valkey stream. Our Drain3 worker parses them into templates on the fly. The feature extractor groups them into 60-second sliding windows. Our Isolation Forest scores the window; if anomalous, the NetworkX graph engine ranks which upstream service triggered the cascade. The entire sequence takes under 120 milliseconds."
  },
  {
    "slide_num": 10,
    "title": "Algorithmic Core: Drain3 & Sliding-Window Isolation Forest",
    "template_ref": "FlutterForecast Slides 12-13 (Algorithmic & System Components)",
    "category": "ML & Algorithm",
    "headline": "Unsupervised Intelligence Without Manual Regex Rules",
    "visual_layout": "3 Numbered System Component Boxes (01 Matrix/Miner, 02 Model Training, 03 Inference/Scoring) matching FlutterForecast Slide 13.",
    "main_content": "[01] Online Template Mining (Drain3)\n\u2022 Parses raw unstructured text into structured log templates in <0.5ms.\n\u2022 Dynamically detects tokens, replaces variables with [<*>], and updates cluster prefix trees.\n\u2022 Delivers 95%+ data compression while retaining semantic forensic fidelity.\n\n[02] Sliding-Window Feature Extractor\n\u2022 60s windows with 30s stride extract 12 numerical & Shannon entropy features:\n  - Statistical: log_count, error_count, warning_count, error_ratio, logs_per_sec\n  - Entropy & Diversity: template_entropy, unique_templates, dominant_template_ratio\n  - Topology: active_services, dominant_service_count, burst_indicator\n\n[03] Unsupervised Isolation Forest & Threshold Sweep\n\u2022 Ensemble of 100 isolation trees isolating anomalies based on path length.\n\u2022 Optimal threshold sweep (-0.075): 0.940 Accuracy, 0.812 Precision, 0.867 Recall, 0.959 ROC-AUC.",
    "key_metrics": "0.959 ROC-AUC | 0.839 F1-Score | 12 Vector Features | 100 Trees",
    "design_style": "3 Numbered component cards (01, 02, 03) with mathematical equation callouts and threshold curves.",
    "speaker_notes": "Our algorithmic core has 3 major components: Component 1 is Drain3, an online parse tree that strips dynamic variables and produces clean templates. Component 2 is our 12-feature sliding window extractor measuring Shannon entropy, error acceleration, and service distribution. Component 3 is our Isolation Forest. Through an exhaustive threshold sweep, we identified -0.075 as the optimal cutoff, achieving an outstanding 0.959 ROC-AUC and 83.9% F1-score with near-zero false alarms."
  },
  {
    "slide_num": 11,
    "title": "Dynamic Causal Topology & Blast-Radius Ranking",
    "template_ref": "FlutterForecast Slide 12 & PlayMap Slide 8",
    "category": "Graph Engine",
    "headline": "Tracing Cascading Failures to Their True Origin",
    "visual_layout": "Split Layout: Left side shows Directed Causal Graph with root node colored red; Right side shows Blast-Radius ranking table.",
    "main_content": "DYNAMIC TOPOLOGY AUTODISCOVERY:\n\u2022 Inter-service HTTP and gRPC request traces automatically populate directed graph edges in NetworkX.\n\u2022 No manual architecture diagrams or static dependency definitions required.\n\nROOT-CAUSE VS. VICTIM SEPARATION:\n\u2022 Temporal Lead-Time Analysis: Detects which service showed initial degradation before downstream errors occurred.\n\u2022 Downstream Symptom Suppression: Automatically silences redundant 504 Gateway Timeouts from dependent services.\n\nQUANTITATIVE BLAST-RADIUS SCORING:\n\u2022 Scores the reachability of downstream services from the failed node.\n\u2022 Computes percentage of dependent microservice fleet impacted (e.g. 66% blast radius across Order & Payment services).",
    "key_metrics": "100% Causal Dependency Autodiscovery | 85%+ Redundant Alert Suppression",
    "design_style": "Cytoscape-inspired graph visualization mockup with highlighted path traversal and blast-radius gauge.",
    "speaker_notes": "When a failure occurs, the hardest question is: 'Who started it?' Downstream services throw errors simply because their upstream calls timed out. LogSentinel builds a dynamic directed graph of your services. By combining temporal anomaly onset with dependency topology, it separates the root-cause culprit from innocent victim services, suppressing over 85% of downstream symptom alerts."
  },
  {
    "slide_num": 12,
    "title": "Problem-Solution Fit Matrix",
    "template_ref": "PlayMap Slide 9 (Mapped Rows with Detailed Paragraphs)",
    "category": "Validation",
    "headline": "Directly Mapping Operational Challenges to LogSentinel Capabilities",
    "visual_layout": "3 Horizontal Rows: Left: Problem Pill -> Right: Solution Pill, with descriptive comparison paragraphs.",
    "main_content": "[Row 1] Unknown Failures & Zero-Day Bugs  --->  Unsupervised Isolation Forest\n\u2022 Problem: Traditional monitoring depends on regex pattern rules. If an engineer doesn't write a rule for a new exception type, it goes undetected.\n\u2022 LogSentinel Fit: Unsupervised Isolation Forest evaluates structural anomalies and entropy shifts, flagging zero-day failure modes without pre-labeled training.\n\n[Row 2] Cascading Outages & Alert Fatigue  --->  Dynamic Causal Topology Ranking\n\u2022 Problem: An auth service database failure causes 20 downstream microservices to alert simultaneously, drowning SREs in symptom noise.\n\u2022 LogSentinel Fit: NetworkX graph traversal isolates the upstream root node (auth-service) and ranks blast radius, suppressing symptom alarms.\n\n[Row 3] Ballooning Log Storage Costs  --->  Drain3 Real-Time Template Mining\n\u2022 Problem: Storing and indexing terabytes of repetitive raw log strings causes exorbitant cloud bills and sluggish dashboard queries.\n\u2022 LogSentinel Fit: Drain3 clusters logs into templates, storing only dynamic parameters. Achieves 95%+ data compression while preserving full diagnostic context.",
    "key_metrics": "95%+ Storage Compression | Zero-Day Anomaly Detection | Instant Root-Cause Pinpointing",
    "design_style": "3 Stacked cards with Problem -> Solution arrows, colored icons, and structured text paragraphs.",
    "speaker_notes": "This matrix demonstrates our precise problem-solution fit. For unknown bugs: our unsupervised model catches anomalies without regex rules. For cascading outages: our causal graph points straight to the root cause and silences symptom alerts. And for soaring cloud logging costs: Drain3 compresses logs by 95% while keeping all diagnostic fidelity."
  },
  {
    "slide_num": 13,
    "title": "Live Demonstration & Chaos Engineering Scenario",
    "template_ref": "PlayMap Slide 8 ('DEMO' Cycle) & FlutterForecast Slide 14",
    "category": "Demonstration",
    "headline": "Real-Time Detection of Cascading Microservice Failure",
    "visual_layout": "4-Step Circular Execution Cycle (Baseline Traffic -> Chaos Injection -> ML Anomaly Detection -> Topology Highlight & Resolution).",
    "main_content": "DEMONSTRATION WORKFLOW (scripts/trigger_demo_incident.py & scripts/demo_live_pipeline.py):\n\nStep 1: Normal Baseline Traffic\n\u2022 500 logs/sec streaming across auth-service, payment-service, and order-service via /ingest-log.\n\u2022 Topology graph shows all green nodes; Isolation Forest anomaly score stays comfortably below -0.075.\n\nStep 2: Simulated Chaos Injection\n\u2022 Chaos script injects database latency spike and connection pool exhaustion into auth-service.\n\u2022 Downstream order-service and payment-service begin logging HTTP 504 timeouts.\n\nStep 3: Real-Time Algorithmic Reaction\n\u2022 Drain3 flags new cluster templates; sliding-window error_ratio spikes past 0.15; Isolation Forest flags anomaly.\n\nStep 4: Real-Time UI Incident Broadcast\n\u2022 WebSocket broadcasts alert to React dashboard: auth-service node turns flashing red, ranked #1 root cause with 66% blast radius!",
    "key_metrics": "<120ms Broadcast Delay | 100% Synthetic Chaos Repeatability | Interactive Live UI",
    "design_style": "Circular 4-stage process diagram with screenshot callout of the React 18 Cytoscape dashboard.",
    "speaker_notes": "In our live demo, we simulate a realistic microservice outage. We run our chaos injection script, which triggers database pool exhaustion in the auth-service. As payment-service and order-service begin throwing timeout errors, LogSentinel's feature worker detects the entropy anomaly within seconds. On the React dashboard, the auth-service node instantly turns red, flagged as the root cause with a 66% blast radius."
  },
  {
    "slide_num": 14,
    "title": "Added Value & Strategic ROI Breakdown",
    "template_ref": "PlayMap Slide 10 (20% Pie / Wheel Chart) & FlutterForecast Slide 15",
    "category": "Value Proposition",
    "headline": "Quantifiable Value Delivered Across 5 Strategic Pillars",
    "visual_layout": "Donut / Pie Chart divided into 5 equal 20% segments with glowing accent colors and central emblem.",
    "main_content": "PROJECT FOCUS:\nLogSentinel delivers an integrated, autonomous observability experience that combines streaming ingestion, unsupervised ML, and dynamic graph theory directly from raw logs.\n\nSTRATEGIC VALUE DISTRIBUTION (20% Each):\n\u2022 20% MTTR Reduction: Slashes incident investigation time from 45+ minutes to under 30 seconds.\n\u2022 20% Alert Fatigue Suppression: Filters out up to 85% of redundant cascading noise to focus on the root trigger.\n\u2022 20% Zero-Configuration ML: Unsupervised learning eliminates brittle rules, manual regex, and model retraining overhead.\n\u2022 20% High-Throughput Cloud Efficiency: Ingests 10,000+ logs/sec per node with ultra-low memory footprint (<52MB RSS).\n\u2022 20% Dynamic Topology Autodiscovery: Automatically maps evolving service architectures without manual documentation.",
    "key_metrics": "5 Strategic Value Pillars (20% Each) | 85% Alert Reduction | 90% MTTR Reduction",
    "design_style": "5-slice colorful Donut Chart: MTTR (Emerald), Noise (Cyan), Unsupervised ML (Blue), Scale (Violet), Topology (Amber).",
    "speaker_notes": "LogSentinel's added value is evenly balanced across 5 critical pillars, each contributing 20% to the overall ROI: First, a 90% reduction in MTTR. Second, suppressing alert fatigue. Third, zero-configuration machine learning that needs no manual rules. Fourth, extreme cloud efficiency running at 10k logs/sec with under 52 megabytes of RAM. And fifth, dynamic service topology discovered automatically."
  },
  {
    "slide_num": 15,
    "title": "Business Model: Tiered Platform Pricing",
    "template_ref": "PlayMap Slide 11 (3 Tiered Subscription Cards) & FlutterForecast Slide 5",
    "category": "Business Model",
    "headline": "Scalable Monetization for Teams of Every Size",
    "visual_layout": "3 Pricing Cards: Free Community ($0), LogSentinel Pro ($49/node/mo), Enterprise Sentinel ($1,499+/mo Custom).",
    "main_content": "[Tier 1] COMMUNITY EDITION \u2014 Free & Open Source (Apache 2.0)\n\u2022 Full Docker Compose turnkey fleet (FastAPI + Valkey + TimescaleDB + React)\n\u2022 Drain3 online template miner & basic Isolation Forest\n\u2022 Up to 1,000 logs/second ingestion limit\n\u2022 Community forum & GitHub issue support\n\n[Tier 2] LOGSENTINEL PRO \u2014 $49 / node / month\n\u2022 Everything in Community, plus:\n\u2022 High-throughput Valkey stream cluster (up to 25,000 logs/sec/node)\n\u2022 Dynamic Causal Graph & Blast-Radius Ranking engine\n\u2022 Multi-tenant data isolation & automated model retraining\n\u2022 Slack, PagerDuty, and Webhook alert integrations\n\n[Tier 3] ENTERPRISE SENTINEL \u2014 $1,499+ / month (Custom SLA)\n\u2022 Everything in Pro, plus:\n\u2022 Enterprise SSO (Microsoft Entra ID & Google OAuth 2.0 native)\n\u2022 eBPF kernel-level distributed tracing collector\n\u2022 Air-gapped / On-Premise VPC deployment support\n\u2022 24/7 Dedicated SRE support & 99.99% uptime SLA",
    "key_metrics": "Free Open-Source Core | $49/node Pro Tier | $1,499+ Enterprise Contract",
    "design_style": "3 Tiered cards: Community (Subtle Gray), Pro (Featured Emerald with 'POPULAR' ribbon), Enterprise (Deep Indigo).",
    "speaker_notes": "Our business model blends product-led open-source adoption with high-margin enterprise licensing: Our Community Edition is open source under Apache 2.0, giving developers instant local adoption. LogSentinel Pro at $49 per node per month unlocks our causal graph, multi-tenancy, and automated retraining. Enterprise Sentinel starts at $1,499/month, adding native Microsoft and Google SSO, eBPF tracing, air-gapped deployment, and 24/7 SLAs."
  },
  {
    "slide_num": 16,
    "title": "Business Model: Add-ons & Professional Services",
    "template_ref": "PlayMap Slide 12 (In-Website Shop & Boosters / Study Tools)",
    "category": "Business Model",
    "headline": "Modular Boosters & High-Value Enterprise Extensions",
    "visual_layout": "2 Structured Columns: Left: Cloud Boosters & Connectors ($199-$499/mo); Right: Enterprise Advisory & Services ($2,500-$10,000).",
    "main_content": "[Category 1] Cloud Boosters & Integrations ($199 - $499 / mo)\n\u2022 Datadog & Splunk Bi-Directional Bridge: Forward parsed templates into existing enterprise SIEMs.\n\u2022 CloudWatch & GCP Cloud Logging Native Sinks: 1-click cloud audit log ingestion.\n\u2022 Long-Term Cold Storage Compactor: S3/GCS Parquet archiving with 99% query compression.\n\u2022 High-Frequency Anomaly Retraining Packs: Hourly adaptive model updates for high-churn clusters.\n\n[Category 2] SRE Advisory & Professional Services ($2,500 - $10,000)\n\u2022 Custom Domain Anomaly Calibration: Tuning Isolation Forest contamination for proprietary protocols.\n\u2022 Chaos Engineering Readiness Audit: Running simulated failure game days to validate resilience.\n\u2022 Production Cutover Assistance: 7-day guided rollout with zero downtime migration runbooks.",
    "key_metrics": "$199-$499 Recurring Add-ons | $2.5k-$10k High-Margin Services | Expand Net Revenue",
    "design_style": "2 Card boxes with shopping/plug icons, pricing tags, and enterprise service checklists.",
    "speaker_notes": "To further monetize and support enterprise accounts, we offer modular add-ons and advisory services. Cloud Boosters range from $199 to $499 a month for cold-storage S3 compactors and SIEM bridges. For mission-critical deployments, our team provides SRE advisory services\u2014calibrating custom models and conducting chaos game days ranging from $2,500 to $10,000 per engagement."
  },
  {
    "slide_num": 17,
    "title": "Future Roadmap & Innovation Horizon",
    "template_ref": "FlutterForecast Slide 16 ('Future Plans')",
    "category": "Roadmap",
    "headline": "The Next Frontier: Autonomous Self-Healing Observability",
    "visual_layout": "3 Sequential Future Milestone Cards with targeted release timelines (Q1 2027, Q2 2027, Q3 2027).",
    "main_content": "Milestone 01: eBPF Kernel-Level Distributed Tracing (Q1 2027)\n\u2022 Zero-overhead kernel socket tracing bypassing application code modification.\n\u2022 Dynamic protocol correlation across gRPC, HTTP/2, and database wire protocols.\n\nMilestone 02: Generative AI Incident Post-Mortems & PR Generation (Q2 2027)\n\u2022 Autonomous LLM synthesis of anomaly windows into executive-ready incident post-mortems.\n\u2022 Automated draft pull requests suggesting configuration tweaks or bug fixes based on stack traces.\n\nMilestone 03: Autonomous Kubernetes Self-Healing Operator (Q3 2027)\n\u2022 Closed-loop remediation: automatically restart pods, shed traffic, or activate circuit breakers when anomaly confidence > 95%.\n\u2022 Eliminates human intervention for known cascading failure patterns.",
    "key_metrics": "Zero-Code eBPF Tracing | Automated GenAI Post-Mortems | Closed-Loop Auto-Remediation",
    "design_style": "3 Modern milestone cards with horizon timeline arrows, AI/Kubernetes icons, and delivery targets.",
    "speaker_notes": "Looking to the future, our roadmap expands LogSentinel into an autonomous self-healing platform: In Q1 2027, we introduce zero-overhead eBPF kernel tracing. In Q2, Generative AI incident post-mortems will automatically write incident reports and draft GitHub PRs. And in Q3, our Kubernetes Operator will close the loop with automated remediation\u2014restarting failed pods and circuit breaking before customers even notice."
  },
  {
    "slide_num": 18,
    "title": "Thank You & Open Q&A",
    "template_ref": "PlayMap Slide 13 / FlutterForecast Slide 17",
    "category": "Closing",
    "headline": "Taming Microservice Chaos Starts Today",
    "visual_layout": "Closing Slide: Bold 'Thank You', Slogan Banner, Team Contacts, GitHub & Documentation Links.",
    "main_content": "THANK YOU FOR LISTENING!\n\n\"Change starts with the steps we take today!\"\nEmpower your SREs. Eliminate alert noise. Tame microservice chaos.\n\nPROJECT RESOURCES & LINKS:\n\u2022 GitHub Repository: github.com/meloshaj/LogSentinel\n\u2022 Open Source License: Apache 2.0\n\u2022 Documentation: /docs (Runbooks, Feature Extraction, Architecture ADRs)\n\u2022 Quickstart: docker compose -f docker-compose.demo.yml up --build -d\n\nCONTACT THE TEAM:\n\u2022 Melos Hajrullahu (Team Lead): hajrullahumelos@gmail.com\n\u2022 Leorent Ismajli, Blerim Haxhiu, Blert Sylejmani, Juled Morina",
    "key_metrics": "Apache 2.0 Open Source | github.com/meloshaj/LogSentinel | Ready for Production",
    "design_style": "High-impact closing banner with vibrant blue/orange starburst accents, contact cards, and GitHub QR placeholder.",
    "speaker_notes": "Thank you so much for your time. LogSentinel is fully open source under the Apache 2.0 license, with Docker Compose scripts ready to spin up right now. We invite you to clone the repo, run our chaos benchmark script, and see the future of autonomous observability in action. We are now open for any questions!"
  }
];

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
  if (initialSlides.length > 0) {
    initialSlides[0].remove();
  }

  for (var i = 0; i < slidesData.length; i++) {
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
    var speakerNotesText = "SLIDE " + data.slide_num + " - " + data.title + "\n\n" +
      "TEMPLATE REFERENCE: " + data.template_ref + "\n\n" +
      "SPEAKER NOTES & TALKING POINTS:\n" + data.speaker_notes;
    notes.getSpeakerNotesShape().getText().setText(speakerNotesText);
  }

  Logger.log("Successfully generated presentation: " + presentation.getUrl());
  return presentation.getUrl();
}

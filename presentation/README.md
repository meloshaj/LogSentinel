# LogSentinel Presentation — Google Slides Export

Based directly on the **PlayMap** and **FlutterForecast** presentation templates, this directory contains the presentation deck for **LogSentinel** exported for **Google Slides**.

---

## 📦 Generated Deliverables

| File | Format | Description |
|---|---|---|
| **[`LogSentinel_Presentation_GoogleSlides.pptx`](./LogSentinel_Presentation_GoogleSlides.pptx)** | **Google Slides / PowerPoint (.pptx)** | **Primary Google Slides Deck**: Native 16:9 widescreen presentation with 18 slides, card shapes, multi-column blocks, milestone steps, color themes, and speaker notes. Ready for 1-click import into Google Slides. |
| **[`generate_google_slides.gs`](./generate_google_slides.gs)** | **Google Apps Script** | Auto-generator script that creates the Google Slides presentation directly in your Google Drive via Google's API. |
| **[`presentation_preview.html`](./presentation_preview.html)** | **Interactive Web Deck** | Interactive slide deck preview in the browser with keyboard arrow navigation (`←` / `→`) and full presenter scripts. |
| **[`LogSentinel_Presentation_GoogleSheets.xlsx`](./LogSentinel_Presentation_GoogleSheets.xlsx)** | **Excel / Google Sheets** | 5-tab spreadsheet workbook with complete slide data, problem-solution fit matrix, tech stack, business tiers, and empirical benchmarks. |
| **[`LogSentinel_Presentation.csv`](./LogSentinel_Presentation.csv)** | **CSV** | Clean tabular CSV slide export. |

---

## 🚀 How to Open in Google Slides (2 Simple Methods)

### Method 1: Open Directly in Google Drive / Google Slides (Easiest - 1 Click)
1. Open [Google Drive](https://drive.google.com) or [Google Slides](https://slides.new).
2. Click **New (+)** ➔ **File upload** (or in Google Slides: **File** ➔ **Import slides** ➔ **Upload**).
3. Select **`LogSentinel_Presentation_GoogleSlides.pptx`**.
4. Double-click the uploaded file in Google Drive. Google Drive will automatically open it as a full native **Google Slides** presentation with all 18 slides, custom cards, colors, and speaker notes intact!

### Method 2: Generate via Google Apps Script
1. Go to [script.google.com](https://script.google.com) (or inside any Google doc, click **Extensions** ➔ **Apps Script**).
2. Create a new project and paste the contents of **[`generate_google_slides.gs`](./generate_google_slides.gs)** into `Code.gs`.
3. Click **Run** (`generateLogSentinelPresentation`) and grant Google permissions.
4. The script will automatically generate a new **Google Slides** document directly in your Google Drive and log the direct link.

---

## 🎨 Slide Deck Overview (18 Slides)

```text
├── Slide 01: Title & Vision (LogSentinel: Taming Microservice Chaos in Real Time)
├── Slide 02: The Engineering Team (5 Core Engineers & Ownership)
├── Slide 03: The Problem (3 Numbered Cards: Cascading Failures, Alert Fatigue, Downtime Cost)
├── Slide 04: Data-Driven Research (82% Cascade Outages, 67% MTTR Wasted, 0.959 ROC-AUC)
├── Slide 05: The LogSentinel Solution (3 Pillars: Ingestion Buffer, Unsupervised ML, Causal Graph)
├── Slide 06: Engineering Lifecycle & Phases (5 Connected Milestone Steps)
├── Slide 07: Technologies: Front-End (3 Vertical Columns: React 18, TailwindCSS, Cytoscape/XYFlow)
├── Slide 08: Technologies: Back-End (3 Vertical Columns: FastAPI, Valkey 8.0, TimescaleDB)
├── Slide 09: End-to-End System Architecture (7-Stage Non-Blocking Stream Pipeline)
├── Slide 10: Algorithmic Core (3 Components: Drain3 Miner, 12-Feature Window, Isolation Forest)
├── Slide 11: Dynamic Causal Topology & Blast Radius (NetworkX Graph & Root-Cause Ranking)
├── Slide 12: Problem-Solution Fit Matrix (3 Mapped Rows: Problem -> Solution)
├── Slide 13: Live Demonstration & Chaos Scenario (4-Stage Chaos Outage & Live UI Reaction)
├── Slide 14: Added Value & Strategic ROI (5-Way 20% Value Wheel)
├── Slide 15: Business Model: Tiered Platform Pricing (Community Free, Pro $49/node, Enterprise $1,499+)
├── Slide 16: Business Model: Add-ons & Boosters (SIEM Bridges, Cold Storage, SRE Chaos Audits)
├── Slide 17: Future Roadmap & Innovation (eBPF Kernel Tracing, GenAI Post-Mortems, K8s Auto-Remediation)
└── Slide 18: Thank You & Contact Information (Closing Slogan, Apache 2.0 Links, Team Contacts)
```

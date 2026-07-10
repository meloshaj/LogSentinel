# LogSentinel Project Contents

Generated on 2026-07-09.

This document inventories the LogSentinel repository: what the project does, the main technologies it uses, the runtime components, and the files currently present in the source tree. Dependency and cache folders are summarized instead of expanded.

## Project Summary

LogSentinel is a real-time log monitoring and anomaly detection platform. It includes:

- A React/Vite dashboard for viewing logs, anomalies, AI analysis, incidents, analytics, and settings.
- A FastAPI ingestion backend that accepts log batches asynchronously.
- Drain3-based log template parsing and clustering.
- PostgreSQL persistence for parsed logs and incidents.
- Sliding-window feature extraction for downstream ML.
- Isolation Forest anomaly detection support.
- Tests, validation scripts, Docker runtime files, and project documentation.

## Technology Stack

### Frontend

- React 18
- React Router 7
- Vite 6
- TypeScript
- Tailwind CSS 4
- Recharts
- lucide-react icons

### Backend

- Python
- FastAPI
- Uvicorn
- Pydantic 2
- SQLAlchemy async
- asyncpg
- Drain3
- NumPy
- scikit-learn
- pytest
- httpx

### Infrastructure

- Docker
- Docker Compose
- PostgreSQL 16 Alpine
- nginx for serving the production frontend container

## Runtime Services

`docker-compose.yml` defines two services:

- `postgres`: PostgreSQL database container named `logsentinel_postgres`, using `scripts/init.sql` for first-boot schema setup.
- `logsentinel`: production dashboard container built from the repository `Dockerfile`, served on port `8080`.

The backend can also be run locally with:

```powershell
cd backend
python -m uvicorn app.main:app --reload
```

## Backend API Surface

The FastAPI backend in `backend/app/main.py` exposes:

- `POST /ingest-log`: accept a batch of logs for asynchronous processing.
- `GET /drain3/stats`: parser, worker, and batch manager statistics.
- `GET /drain3/recent`: recent parsed logs.
- `GET /drain3/templates`: known Drain3 templates.
- `POST /drain3/flush`: force pending parsed logs to flush to the sink.
- `GET /drain3/db-health`: non-throwing PostgreSQL health check.
- `GET /features/stats`: feature extraction worker statistics.
- `GET /features/recent`: recent extracted feature vectors.
- `POST /features/extract`: manually trigger pending feature extraction.

## Database Schema

`scripts/init.sql` initializes:

- `severity_level` enum: `INFO`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.
- `incident_status` enum: `OPEN`, `INVESTIGATING`, `MITIGATED`, `RESOLVED`.
- `logs` table for raw and parsed log events, template data, parameters, correlation IDs, metadata, and timestamps.
- `incidents` table for anomaly incidents, root causes, severity, blast radius, status, and timestamps.
- Indexes for template lookup, correlation IDs, timestamps, recency, and incident status.

## Directory Inventory

### Root

| Path | Purpose |
| --- | --- |
| `.dockerignore` | Docker build ignore rules. |
| `.gitignore` | Git ignore rules. |
| `Dockerfile` | Multi-stage frontend production image, served through nginx. |
| `docker-compose.yml` | Local PostgreSQL and production dashboard orchestration. |
| `FEATURE_EXTRACTION_SUMMARY.md` | Summary of feature extraction implementation. |
| `IMPLEMENTATION_SUMMARY.md` | High-level implementation notes. |
| `index.html` | Vite HTML entry point. |
| `LICENSE` | Project license. |
| `nginx.conf` | nginx config for serving the built dashboard. |
| `package.json` | Frontend package metadata, scripts, and dependencies. |
| `pnpm-lock.yaml` | Locked pnpm dependency graph. |
| `pnpm-workspace.yaml` | pnpm workspace configuration. |
| `postcss.config.mjs` | PostCSS/Tailwind configuration. |
| `PREREQUISITES_CHECKLIST.md` | Prerequisite checklist documentation. |
| `README.md` | Main project overview and run instructions. |
| `vite.config.ts` | Vite configuration. |

### `backend/`

| Path | Purpose |
| --- | --- |
| `backend/requirements.txt` | Python backend dependencies. |
| `backend/app/__init__.py` | Backend app package marker. |
| `backend/app/main.py` | FastAPI app, ingestion models, workers, routes, and lifespan startup/shutdown. |
| `backend/app/models.py` | Backend domain and persistence models. |
| `backend/app/drain3.ini` | Drain3 parser configuration. |
| `backend/app/core/__init__.py` | Core package marker and exports. |
| `backend/app/core/database.py` | Async database engine/session configuration. |
| `backend/app/core/transaction.py` | Transaction helper utilities. |
| `backend/app/ml/__init__.py` | ML package marker and exports. |
| `backend/app/ml/anomaly_detector.py` | Isolation Forest anomaly detector wrapper. |
| `backend/app/ml/feature_extraction.py` | Feature extraction data structures and logic. |
| `backend/app/ml/feature_extractor.py` | Sliding-window feature extractor implementation. |
| `backend/app/ml/train_isolation_forest.py` | Isolation Forest model training script. |
| `backend/app/repositories/__init__.py` | Repository package marker. |
| `backend/app/repositories/db_health.py` | Database health check helper. |
| `backend/app/repositories/log_repository.py` | Parsed log persistence repository. |
| `backend/app/services/__init__.py` | Service package marker. |
| `backend/app/services/batch_manager.py` | Async parsed-log batch manager and flushing logic. |
| `backend/app/services/drain_parser.py` | Drain3 parser service wrapper. |
| `backend/app/workers/__init__.py` | Worker package marker. |
| `backend/app/workers/drain_worker.py` | Background worker that drains queued logs through Drain3 and batching. |
| `backend/app/workers/feature_worker.py` | Background worker that extracts features and optionally predicts anomalies. |

### `src/`

| Path | Purpose |
| --- | --- |
| `src/main.tsx` | React application entry point. |
| `src/App.tsx` | Root app component using `RouterProvider`. |
| `src/routes.tsx` | Browser routes for dashboard pages. |
| `src/types/monitoring.ts` | Shared frontend monitoring types. |
| `src/services/mockMonitoringData.ts` | Mock data used by the dashboard UI. |
| `src/constants/navigation.ts` | Sidebar/navigation item configuration. |
| `src/constants/pageMeta.ts` | Page metadata configuration. |
| `src/constants/statusConfig.ts` | Status/severity visual configuration. |
| `src/hooks/useClock.ts` | Clock hook. |
| `src/hooks/useLiveLogs.ts` | Live log stream hook. |
| `src/hooks/useThemeMode.ts` | Theme mode hook. |
| `src/hooks/useToggle.ts` | Boolean toggle hook. |
| `src/layouts/RootLayout.tsx` | Main dashboard layout shell. |
| `src/layouts/Sidebar.tsx` | Sidebar navigation component. |
| `src/pages/OverviewPage.tsx` | Overview dashboard page. |
| `src/pages/LogsPage.tsx` | Logs page. |
| `src/pages/AnomaliesPage.tsx` | Anomalies page. |
| `src/pages/AIAnalysisPage.tsx` | AI analysis page. |
| `src/pages/IncidentsPage.tsx` | Incidents page. |
| `src/pages/AnalyticsPage.tsx` | Analytics page. |
| `src/pages/SettingsPage.tsx` | Settings page. |
| `src/components/common/DependencyGraph.tsx` | Dependency graph visualization. |
| `src/components/common/Panel.tsx` | Shared panel component. |
| `src/components/dashboard/AnomalyPanel.tsx` | Dashboard anomaly panel. |
| `src/components/dashboard/MetricCards.tsx` | Dashboard metric cards. |
| `src/components/dashboard/RootCausePanel.tsx` | Root cause ranking panel. |
| `src/components/dashboard/TrafficChart.tsx` | Traffic chart component. |
| `src/components/incidents/IncidentsPanel.tsx` | Incidents panel. |
| `src/components/logs/LogStream.tsx` | Log stream display component. |
| `src/styles/fonts.css` | Font stylesheet placeholder. |
| `src/styles/globals.css` | Global stylesheet placeholder. |
| `src/styles/index.css` | Main stylesheet imports. |
| `src/styles/tailwind.css` | Tailwind stylesheet imports. |
| `src/styles/theme.css` | Dashboard theme and component styling. |

### `docs/`

| Path | Purpose |
| --- | --- |
| `docs/FEATURE_EXTRACTION.md` | Full feature extraction documentation. |
| `docs/PREREQUISITES_COMPLETED.md` | Completed prerequisite documentation. |
| `docs/QUICK_START_FEATURES.md` | Quick start guide for feature extraction. |

### `guidelines/`

| Path | Purpose |
| --- | --- |
| `guidelines/Guidelines.md` | Project guidelines. |

### `scripts/`

| Path | Purpose |
| --- | --- |
| `scripts/demo_drain3_e2e.py` | End-to-end Drain3 demo script. |
| `scripts/init.sql` | PostgreSQL schema initialization. |
| `scripts/smoke_insert_drain3_db.py` | Direct smoke test for inserting parsed logs into PostgreSQL. |
| `scripts/test_feature_extraction.py` | Script for testing feature extraction behavior. |
| `scripts/validate_prerequisites.py` | Prerequisite validation script. |
| `scripts/verify_drain3_db.py` | Drain3 database verification script. |

### `tests/`

| Path | Purpose |
| --- | --- |
| `tests/test_anomaly_detector.py` | Tests for Isolation Forest anomaly detection. |
| `tests/test_batch_manager.py` | Tests for parsed-log batching and flushing. |
| `tests/test_drain_parser.py` | Tests for Drain3 parser behavior. |
| `tests/test_drain_worker.py` | Tests for background Drain3 worker behavior. |
| `tests/test_feature_extractor.py` | Tests for feature extraction. |
| `tests/test_ingest_gateway.py` | Tests for ingestion gateway API behavior. |
| `tests/test_log_repository.py` | Tests for parsed log repository persistence. |

### `state/`

| Path | Purpose |
| --- | --- |
| `state/drain3_state.bin` | Persisted Drain3 parser state. |

## Generated, Installed, Or Cached Content

The repository currently also contains local/generated directories:

- `.git/`: Git metadata.
- `.pnpm-store/`: pnpm package store.
- `.pytest_cache/`: pytest cache.
- `node_modules/`: installed frontend dependencies.
- `__pycache__/` directories under `backend/`, `scripts/`, and `tests/`: Python bytecode caches.

These are environment artifacts rather than hand-authored project source. They are intentionally summarized here instead of listed file-by-file.

## File Count Summary

Ignoring `.git`, `node_modules`, `.pnpm-store`, `.pytest_cache`, and Python `__pycache__` directories, the source tree contains:

- Root/config/documentation files.
- Backend Python source under `backend/app`.
- Frontend React/TypeScript source under `src`.
- Project documentation under `docs` and `guidelines`.
- Utility and validation scripts under `scripts`.
- Pytest tests under `tests`.
- Drain3 runtime state under `state`.

## Main Workflows

### Frontend Development

```powershell
pnpm install
pnpm dev
```

### Frontend Production Build

```powershell
pnpm build
```

### Backend Development

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker Runtime

```powershell
docker compose up --build
```

### Backend Test Suite

```powershell
pytest
```

### Drain3 End-to-End Demo

```powershell
python scripts/demo_drain3_e2e.py
python scripts/verify_drain3_db.py
```


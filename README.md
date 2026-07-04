# LogSentinel

LogSentinel is an automated, real-time log anomaly detection and root-cause ranking platform. Engineered to bypass manual threshold tuning and static alerting parameters, it continuously ingests raw log data, groups similar messages into structured templates using lightweight unsupervised machine learning, and flags system deviations dynamically.

## ✨ New: Feature Extraction Module

LogSentinel now includes a **production-ready sliding-window feature extraction module** that extracts statistical and semantic features from parsed log streams for downstream anomaly detection and ML models.

**Key Features:**
- 🪟 Time-based sliding windows with configurable size and overlap
- 📊 Statistical features: log counts, error rates, template diversity
- 🔢 ML-ready feature arrays for scikit-learn integration
- ⚡ Real-time extraction with async processing
- 🎯 Type-safe Pydantic models throughout
- 📡 REST API for monitoring and control

**Quick Start:**
```bash
# Start backend with feature extraction enabled (runs automatically)
cd backend
python -m uvicorn app.main:app --reload

# Send logs
python scripts/demo_drain3_e2e.py

# Get extracted features
curl http://localhost:8000/features/recent
```

**Documentation:**
- Quick Start: [`docs/QUICK_START_FEATURES.md`](docs/QUICK_START_FEATURES.md)
- Full Documentation: [`docs/FEATURE_EXTRACTION.md`](docs/FEATURE_EXTRACTION.md)
- Implementation Summary: [`FEATURE_EXTRACTION_SUMMARY.md`](FEATURE_EXTRACTION_SUMMARY.md)

## Docker

Build and run the production container:

```bash
docker compose up --build
```

The app will be available at `http://localhost:8080`.

Build the image directly:

```bash
docker build -t logsentinel-dashboard .
docker run --rm -p 8080:80 logsentinel-dashboard
```

## Backend Drain3 Runtime Verification

### Path A: Docker PostgreSQL

Start PostgreSQL from Windows PowerShell:

```powershell
docker compose up -d postgres
```

Run the backend API locally:

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

In a second PowerShell window, run the end-to-end demo from the repository root:

```powershell
python scripts/demo_drain3_e2e.py
```

The demo sends fewer than 500 logs, waits for the 5-second periodic flush, calls `/drain3/stats`, calls `/drain3/templates`, and finishes with a safety flush:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/drain3/flush
```

Verify the Drain3 database schema and latest rows without using `docker exec`:

```powershell
python scripts/verify_drain3_db.py
```

Smoke test `LogRepository` directly without running FastAPI:

```powershell
python scripts/smoke_insert_drain3_db.py
python scripts/verify_drain3_db.py
```

You can also verify inserted rows through `psql` in the Docker container:

```powershell
docker exec -it logsentinel_postgres psql -U logsentinel -d logsentinel_db -c "SELECT id, service, raw_message, template_id, template_text, parameters, correlation_id, parsed_at FROM logs ORDER BY created_at DESC LIMIT 10;"
```

### Path B: Existing PostgreSQL / No Docker

Point the backend and verification scripts at an existing PostgreSQL database:

```powershell
$env:DATABASE_URL="postgresql+asyncpg://logsentinel:logsentinel@127.0.0.1:5432/logsentinel_db"
```

Run the backend:

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

In a second PowerShell window from the repository root, use the same `DATABASE_URL` and run:

```powershell
$env:DATABASE_URL="postgresql+asyncpg://logsentinel:logsentinel@127.0.0.1:5432/logsentinel_db"
python scripts/demo_drain3_e2e.py
python scripts/verify_drain3_db.py
python scripts/smoke_insert_drain3_db.py
```

The backend also exposes a non-throwing DB health endpoint:

```powershell
Invoke-RestMethod -Method Get -Uri http://localhost:8000/drain3/db-health
```

### Troubleshooting

If PowerShell says `docker` is not recognized, start Docker Desktop or install Docker and reopen the terminal so `docker compose` is on `PATH`.

If PostgreSQL starts but inserts fail because columns such as `template_text`, `parameters`, or `parsed_at` are missing, the local Docker volume was probably created before the Drain3 schema was added. For local development only, recreate it:

```powershell
docker compose down
docker volume rm logsentinel_logsentinel_pgdata
docker compose up -d postgres
```

If the volume name differs, list volumes with:

```powershell
docker volume ls
```

If `python -m uvicorn app.main:app` cannot import `app`, make sure the command is run from the `backend` directory. From the repository root, use:

```powershell
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

If batch stats show `last_sink_error` with `Connect call failed ('127.0.0.1', 5432)`, PostgreSQL is not reachable from the backend process. Check that the container is running and healthy:

```powershell
docker ps --filter "name=logsentinel_postgres"
docker compose logs postgres
```

If credentials or ports were changed, set the matching environment variables before starting the backend:

```powershell
$env:POSTGRES_USER = "logsentinel"
$env:POSTGRES_PASSWORD = "logsentinel_secret"
$env:POSTGRES_DB = "logsentinel_db"
$env:POSTGRES_HOST = "localhost"
$env:POSTGRES_PORT = "5432"
```

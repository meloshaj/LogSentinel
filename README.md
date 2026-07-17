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

```powershell
# Start backend with feature extraction enabled (runs automatically)
cd backend
python -m uvicorn app.main:app --reload

# In a second terminal from the repository root, set a local ingestion key and send logs
$env:INGEST_API_KEY="dev-local-key"
python scripts/demo_drain3_e2e.py

# Get extracted features
curl http://localhost:8000/features/recent
```

`POST /ingest-log` requires an `X-API-Key` header. Configure local development with `INGEST_API_KEY` or comma-separated `INGEST_API_KEYS`; do not commit real keys to source control.

```powershell
$env:INGEST_API_KEY="dev-local-key"

curl -X POST http://localhost:8000/ingest-log `
  -H "Content-Type: application/json" `
  -H "X-API-Key: dev-local-key" `
  -d '{"source":"test","logs":[{"service_name":"test","message":"test"}]}'
```

## Dashboard Authentication

The React dashboard uses the backend JWT endpoints under `/api/auth` for user login and registration:

- `POST /api/auth/register` creates a user record with a bcrypt-hashed password.
- `POST /api/auth/login` verifies the password and returns a bearer JWT.
- `GET /api/auth/me` verifies `Authorization: Bearer <token>` and returns the current user.

The frontend stores the JWT in `localStorage` as `authToken`, and protected dashboard routes treat that token as the only source of truth. Logout clears `authToken` and redirects back to `/login`. The legacy `isLoggedIn` flag is cleared when auth state changes and is not used for access decisions.

Configure JWT signing with `JWT_SECRET_KEY`. The backend has a development fallback so local startup is easy, but production deployments must set a strong secret. Tokens are signed with `HS256` and currently expire after 60 minutes.

Because `authToken` is stored in `localStorage`, it persists across browser refreshes and is readable by JavaScript running on the page. Keep the frontend free of XSS issues, avoid storing other secrets in the browser, and consider an HttpOnly cookie strategy before treating this as a hardened production auth model.

This user dashboard JWT flow is separate from the machine-to-machine ingestion guard. `/ingest-log` continues to use the `X-API-Key` header configured by `INGEST_API_KEY` or `INGEST_API_KEYS`; dashboard JWTs are not accepted as ingestion API keys.

**Documentation:**

- Quick Start: [`docs/QUICK_START_FEATURES.md`](docs/QUICK_START_FEATURES.md)
- Full Documentation: [`docs/FEATURE_EXTRACTION.md`](docs/FEATURE_EXTRACTION.md)
- Implementation Summary: [`FEATURE_EXTRACTION_SUMMARY.md`](FEATURE_EXTRACTION_SUMMARY.md)

## WebSocket Telemetry

The backend exposes a lightweight WebSocket telemetry stream at:

```text
ws://localhost:8000/ws/telemetry
```

The React dashboard uses `VITE_WS_URL` when it is set, otherwise it falls back to `ws://localhost:8000/ws/telemetry`.

```powershell
$env:VITE_WS_URL="ws://localhost:8000/ws/telemetry"
```

Supported first-version event types:

- `system.status`
- `log.parsed`
- `feature.window.closed`
- `anomaly.detected`, only when an existing anomaly prediction is available and marks the window as anomalous

Manual telemetry smoke test:

```powershell
# Terminal 1
cd backend
$env:INGEST_API_KEY="dev-local-key"
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2
$env:VITE_WS_URL="ws://localhost:8000/ws/telemetry"
pnpm dev

# Terminal 3, from the repository root
$env:INGEST_API_KEY="dev-local-key"
python scripts/demo_drain3_e2e.py
```

Open the Logs page and verify that `system.status` appears on connection, `log.parsed` appears as logs are parsed, and `feature.window.closed` appears after feature windows close.

For a fuller presentation flow using synthetic logs through the real backend pipeline, see [`docs/DEMO_LIVE_PIPELINE.md`](docs/DEMO_LIVE_PIPELINE.md).

## Docker

Build and run the production container:

```bash
docker compose up --build
```

The app will be available at `http://localhost:8080`.

## WebSocket

Live logs are pulled from a backend WebSocket endpoint. Set `VITE_WS_URL` before starting the frontend if your server is not reachable at the default same-origin `/ws/logs` path.

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
$env:INGEST_API_KEY="dev-local-key"
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

In a second PowerShell window, run the end-to-end demo from the repository root:

```powershell
$env:INGEST_API_KEY="dev-local-key"
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
$env:INGEST_API_KEY="dev-local-key"
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

In a second PowerShell window from the repository root, use the same `DATABASE_URL` and run:

```powershell
$env:DATABASE_URL="postgresql+asyncpg://logsentinel:logsentinel@127.0.0.1:5432/logsentinel_db"
$env:INGEST_API_KEY="dev-local-key"
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

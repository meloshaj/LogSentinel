# Live Pipeline Demo

This demo uses synthetic generated logs, but they flow through the real LogSentinel backend ingestion, parsing, persistence, feature extraction, and WebSocket telemetry pipeline. It does not use hardcoded frontend mock telemetry.

## Purpose

`scripts/demo_live_pipeline.py` is a presentation and manual QA utility. It sends synthetic `auth-service`, `payment-service`, and `order-service` logs to the real `/ingest-log` endpoint so the React dashboard can show live WebSocket telemetry from the backend.

The flow is:

```text
Synthetic service logs
-> POST /ingest-log with X-API-Key
-> ingestion queue
-> Drain3 parsing
-> PostgreSQL persistence
-> sliding-window feature extraction
-> WebSocket telemetry
-> React dashboard live update
```

## Environment Variables

```powershell
$env:INGEST_API_KEY="dev-local-key"
$env:LOGSENTINEL_API_URL="http://localhost:8000"
$env:VITE_WS_URL="ws://localhost:8000/ws/telemetry"
```

`INGEST_API_KEY` is required. Do not commit real API keys.

`LOGSENTINEL_API_URL` is optional and defaults to `http://localhost:8000`.

## Start The Backend

```powershell
cd backend
$env:INGEST_API_KEY="dev-local-key"
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Start The Frontend

From the repository root:

```powershell
$env:VITE_WS_URL="ws://localhost:8000/ws/telemetry"
pnpm dev
```

Open the Logs page in the dashboard.

## Run The Demo

From the repository root:

```powershell
$env:INGEST_API_KEY="dev-local-key"
$env:LOGSENTINEL_API_URL="http://localhost:8000"
python scripts/demo_live_pipeline.py
```

The script prints each batch number, number of logs sent, HTTP status code, and the backend `accepted` value when present. After sending logs, it tries to trigger `POST /features/extract` and then queries `/drain3/recent` and `/features/recent` for a short verification summary.

## Dashboard Verification

On the Logs page, verify:

- `system.status` appears when the dashboard connects to `/ws/telemetry`.
- `log.parsed` events appear while the script sends batches.
- `feature.window.closed` events appear when feature windows close or manual extraction succeeds.
- Recent parsed logs include `auth-service`, `payment-service`, and `order-service`.
- Error bursts are visible through repeated timeout/error patterns.

## Cleanup

This script is isolated under scripts/ and is not imported by runtime code. It can be removed later with:

```powershell
git rm scripts/demo_live_pipeline.py
git rm docs/DEMO_LIVE_PIPELINE.md
```

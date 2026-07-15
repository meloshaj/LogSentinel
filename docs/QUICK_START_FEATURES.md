# Quick Start: Feature Extraction

This guide shows how to use the new feature extraction capabilities in LogSentinel.

## Prerequisites ✅

All prerequisites are implemented and tested:
- ✅ Pydantic models (`ParsedLog`, `LogWindow`, `FeatureVector`)
- ✅ ML module structure (`backend/app/ml/`)
- ✅ Feature extraction worker (runs automatically)
- ✅ Integration with Drain3 pipeline
- ✅ API endpoints for monitoring

## 1. Start the Backend

```bash
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

For local ingestion, configure an API key before starting the backend:

```powershell
$env:INGEST_API_KEY="dev-local-key"
```

Use a local value only. Do not commit real API keys to source control. For simple key rotation, set comma-separated `INGEST_API_KEYS`.

You should see:
```
INFO:logsentinel.feature_worker:FeatureExtractionWorker initialized: interval=10s window=60s stride=30s
INFO:logsentinel.feature_worker:FeatureExtractionWorker started
```

## 2. Verify Feature Worker is Running

```bash
curl http://localhost:8000/features/stats
```

Expected response:
```json
{
  "running": true,
  "extraction_interval_seconds": 10.0,
  "features_extracted": 0,
  "extraction_errors": 0,
  "last_extraction_at": null,
  "feature_buffer_size": 0,
  "extractor": {
    "config": {
      "window_size_seconds": 60,
      "stride_seconds": 30,
      "min_logs_per_window": 5
    },
    "buffer_size": 0,
    "logs_processed": 0,
    "windows_generated": 0
  }
}
```

## 3. Send Sample Logs

Use the demo script to send logs:

```powershell
# From the repository root
$env:INGEST_API_KEY="dev-local-key"
python scripts/demo_drain3_e2e.py
```

Or send logs manually:

```bash
curl -X POST http://localhost:8000/ingest-log \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-local-key" \
  -d '{
    "source": "test",
    "environment": "local",
    "logs": [
      {
        "service_name": "auth-service",
        "level": "info",
        "message": "user alice logged in from 192.168.1.100"
      },
      {
        "service_name": "auth-service",
        "level": "error",
        "message": "failed to authenticate user bob"
      }
    ]
  }'
```

## 4. Wait for Feature Extraction

The feature worker extracts features every 10 seconds. Wait 10-15 seconds after sending logs.

## 5. View Extracted Features

```bash
# Get recent feature vectors
curl http://localhost:8000/features/recent?limit=5

# Get updated statistics
curl http://localhost:8000/features/stats
```

Expected response from `/features/recent`:
```json
{
  "features": [
    {
      "window_id": "window-abc123...",
      "timestamp": "2026-07-04T12:34:56.789Z",
      "log_count": 16,
      "unique_templates": 4,
      "error_count": 2,
      "warning_count": 0,
      "template_frequencies": {
        "cluster-1": 0.625,
        "cluster-2": 0.25,
        "cluster-3": 0.125
      },
      "template_entropy": 1.299,
      "service_distribution": {
        "auth-service": 10,
        "payment-service": 6
      },
      "logs_per_second": 0.267,
      "feature_array": [16.0, 4.0, 2.0, 0.0, 1.299, 0.267, 2.0],
      "feature_names": [
        "log_count",
        "unique_templates", 
        "error_count",
        "warning_count",
        "template_entropy",
        "logs_per_second",
        "num_services"
      ]
    }
  ]
}
```

## 6. Manual Feature Extraction

Trigger feature extraction immediately (without waiting for the periodic timer):

```bash
curl -X POST http://localhost:8000/features/extract
```

Response:
```json
{
  "features_extracted": 2,
  "features": [
    {
      "window_id": "window-xyz...",
      "log_count": 10,
      ...
    }
  ]
}
```

## Configuration

Edit `backend/app/main.py` to customize window configuration:

```python
window_config = WindowConfig(
    window_size_seconds=60,      # Change window size
    stride_seconds=30,            # Change overlap
    min_logs_per_window=5,        # Change minimum logs required
    max_logs_per_window=10000,    # Change maximum logs per window
    service_filter=None,          # Optional: filter by service
)

feature_worker = FeatureExtractionWorker(
    window_config=window_config,
    extraction_interval_seconds=10.0,  # Change extraction frequency
    feature_buffer_size=1000,          # Change feature buffer size
)
```

## Understanding Features

### Statistical Features
- **log_count**: Total logs in window (useful for detecting traffic spikes)
- **unique_templates**: Number of distinct log patterns (high = diverse activity)
- **error_count**: Error-level logs (spike = potential incident)
- **warning_count**: Warning-level logs

### Template Features
- **template_frequencies**: Distribution of log patterns (normalized 0-1)
- **template_entropy**: Randomness measure (0 = all same pattern, high = diverse patterns)

### Service Features
- **service_distribution**: Logs per service (detect service-specific issues)

### Temporal Features
- **logs_per_second**: Log rate (detect traffic changes)

## Common Use Cases

### 1. Detect Traffic Spikes

Look for windows with high `log_count` and `logs_per_second`:

```python
if features.logs_per_second > normal_baseline * 2:
    alert("Traffic spike detected")
```

### 2. Detect Error Bursts

Look for windows with high `error_count` relative to `log_count`:

```python
error_rate = features.error_count / features.log_count
if error_rate > 0.1:  # More than 10% errors
    alert("Error burst detected")
```

### 3. Detect Unusual Patterns

Look for windows with high `template_entropy` (many different log patterns):

```python
if features.template_entropy > normal_baseline * 1.5:
    alert("Unusual log pattern diversity")
```

### 4. Service-Specific Monitoring

Create separate windows per service:

```python
auth_config = WindowConfig(service_filter="auth-service")
payment_config = WindowConfig(service_filter="payment-service")
```

## Troubleshooting

### No features are generated

**Check 1**: Are logs being ingested?
```bash
curl http://localhost:8000/drain3/stats
# Look for: "processed_count" > 0
```

**Check 2**: Does the window have enough logs?
```bash
curl http://localhost:8000/features/stats
# Check: "buffer_size" and "min_logs_per_window"
```

**Check 3**: Is the feature worker running?
```bash
curl http://localhost:8000/features/stats
# Look for: "running": true
```

### Feature array is all zeros

This is normal for empty windows. Windows with `log_count: 0` will have zero-filled features.

### Features seem delayed

This is expected. The feature worker extracts features every 10 seconds (configurable). Additionally, windows only close when `end_time < current_time`, so there's a natural delay.

To reduce delay:
- Decrease `extraction_interval_seconds`
- Decrease `window_size_seconds`

## Next Steps

Now that feature extraction is working, you can:

1. **Store features in database**: Extend to persist `FeatureVector` to PostgreSQL
2. **Train ML models**: Use `feature_array` for anomaly detection
3. **Build dashboards**: Visualize feature trends over time
4. **Create alerts**: Set thresholds on specific features
5. **Add custom features**: Extend `extract_features()` with domain-specific metrics

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/features/stats` | GET | Worker and extractor statistics |
| `/features/recent?limit=N` | GET | Recent feature vectors (default: 50) |
| `/features/extract` | POST | Manually trigger extraction |
| `/drain3/stats` | GET | Drain3 parsing statistics |
| `/drain3/recent?limit=N` | GET | Recent parsed logs |
| `/drain3/templates` | GET | Discovered log templates |

## Testing

Run the full test suite:

```bash
python scripts/test_feature_extraction.py
```

All tests should pass:
```
======================================================================
  All Tests Passed ✓
======================================================================

Prerequisites are ready for production feature extraction.
```

## Support

For detailed documentation:
- Architecture: `docs/FEATURE_EXTRACTION.md`
- Implementation summary: `docs/PREREQUISITES_COMPLETED.md`
- Original README: `README.md`

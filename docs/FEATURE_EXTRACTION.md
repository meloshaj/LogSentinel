# Feature Extraction Module

## Overview

The LogSentinel feature extraction module implements a sliding window-based approach to extract statistical and semantic features from parsed log streams. This module is designed to feed downstream anomaly detection and root cause analysis models.

## Architecture

### Components

```
backend/app/
├── models.py                    # Pydantic models (ParsedLog, LogWindow, FeatureVector)
├── ml/
│   ├── __init__.py
│   └── feature_extraction.py   # SlidingWindowExtractor, WindowConfig
└── workers/
    ├── drain_worker.py          # Drain3 parsing (feeds feature worker)
    └── feature_worker.py        # FeatureExtractionWorker (runs extraction loop)
```

### Data Flow

```
1. Log Ingestion (POST /ingest-log)
   └─> AsyncLogBuffer (queue)
       └─> DrainWorker.process_one()
           └─> DrainParser.parse()
               └─> ParsedLog (Pydantic model)
                   ├─> LogRepository (bulk insert to PostgreSQL)
                   └─> FeatureExtractionWorker.add_parsed_log()
                       └─> SlidingWindowExtractor (buffer)
                           └─> Periodic extraction (every 10s)
                               └─> LogWindow → FeatureVector
```

## Models

### ParsedLog

Standardized structure for logs that have been processed through Drain3:

```python
class ParsedLog(BaseModel):
    timestamp: datetime          # When the log was emitted
    service: str                 # Service name
    level: str                   # Severity (info, warning, error)
    raw_message: str             # Original log message
    template_id: str             # Drain3 cluster ID
    template_text: Optional[str] # Template with wildcards
    parameters: list[dict]       # Extracted parameters
    cluster_size: Optional[int]  # Drain3 cluster size
    change_type: Optional[str]   # Drain3 change type
    correlation_id: Optional[str]
    metadata: dict
    parsed_at: Optional[datetime]
```

**All required fields for feature extraction are present:**
- ✅ timestamp
- ✅ service
- ✅ level
- ✅ raw_message
- ✅ template_id
- ✅ template_text

### LogWindow

A time-based sliding window of parsed logs:

```python
class LogWindow(BaseModel):
    window_id: str               # Unique identifier
    start_time: datetime         # Window start (inclusive)
    end_time: datetime           # Window end (exclusive)
    logs: list[ParsedLog]        # Logs in this window
    service: Optional[str]       # Service filter (if any)
    
    # Helper methods
    def log_count() -> int
    def duration_seconds() -> float
    def template_distribution() -> dict[str, int]
```

### FeatureVector

Features extracted from a log window:

```python
class FeatureVector(BaseModel):
    window_id: str
    timestamp: datetime
    
    # Statistical features
    log_count: int
    unique_templates: int
    error_count: int
    warning_count: int
    
    # Template features
    template_frequencies: dict[str, float]
    template_entropy: Optional[float]  # Shannon entropy
    
    # Service features
    service_distribution: dict[str, int]
    
    # Temporal features
    logs_per_second: Optional[float]
    
    # ML-ready features
    feature_array: Optional[list[float]]
    feature_names: Optional[list[str]]
```

## Configuration

### WindowConfig

Configure sliding window behavior:

```python
class WindowConfig(BaseModel):
    window_size_seconds: int = 60      # Window size (default: 60s)
    stride_seconds: int = 30           # Stride between windows (default: 30s)
    min_logs_per_window: int = 1       # Min logs to emit window (0 = no min)
    max_logs_per_window: int = 10000   # Max logs per window
    service_filter: Optional[str]      # Optional service filter
```

**Example configurations:**

```python
# 1-minute windows with 50% overlap
config = WindowConfig(
    window_size_seconds=60,
    stride_seconds=30,
    min_logs_per_window=5,
)

# 5-minute windows with no overlap
config = WindowConfig(
    window_size_seconds=300,
    stride_seconds=300,
    min_logs_per_window=10,
)

# Per-service windows
config = WindowConfig(
    window_size_seconds=60,
    stride_seconds=30,
    service_filter="auth-service",
)
```

## Usage

### Standalone Usage

```python
from backend.app.ml.feature_extraction import SlidingWindowExtractor, WindowConfig
from backend.app.models import ParsedLog

# Configure extractor
config = WindowConfig(window_size_seconds=60, stride_seconds=30)
extractor = SlidingWindowExtractor(config)

# Add parsed logs
log = ParsedLog(
    timestamp=datetime.now(timezone.utc),
    service="auth-service",
    level="info",
    raw_message="user logged in",
    template_id="cluster-1",
    template_text="user logged in",
)
extractor.add_log(log)

# Extract windows
windows = extractor.get_pending_windows()

# Extract features from each window
for window in windows:
    features = extractor.extract_features(window)
    print(f"Window {window.window_id}: {features.log_count} logs")
```

### Integrated Usage (Production)

The feature extraction worker runs automatically when the backend starts:

```python
# In main.py (already configured)
feature_worker = FeatureExtractionWorker(
    window_config=WindowConfig(
        window_size_seconds=60,
        stride_seconds=30,
        min_logs_per_window=5,
    ),
    extraction_interval_seconds=10.0,
)

# Worker starts with application lifespan
drain_worker = DrainWorker(
    ...,
    on_log_parsed=feature_worker.add_parsed_log,  # Automatic feeding
)
```

## API Endpoints

### GET /features/stats

Get feature extraction worker statistics:

```bash
curl http://localhost:8000/features/stats
```

Response:
```json
{
  "running": true,
  "extraction_interval_seconds": 10.0,
  "features_extracted": 42,
  "extraction_errors": 0,
  "last_extraction_at": "2026-07-04T12:34:56.789Z",
  "feature_buffer_size": 42,
  "extractor": {
    "config": {
      "window_size_seconds": 60,
      "stride_seconds": 30,
      "min_logs_per_window": 5
    },
    "buffer_size": 1523,
    "logs_processed": 1523,
    "windows_generated": 42
  }
}
```

### GET /features/recent

Get recently extracted feature vectors:

```bash
curl http://localhost:8000/features/recent?limit=10
```

### POST /features/extract

Manually trigger feature extraction:

```bash
curl -X POST http://localhost:8000/features/extract
```

## Testing

Run the prerequisite test suite:

```bash
cd LogSentinel
python scripts/test_feature_extraction.py
```

Tests validate:
1. ParsedLog Pydantic model
2. WindowConfig validation
3. Sliding window extraction logic
4. Feature vector computation
5. Extractor statistics

## Feature Descriptions

### Statistical Features

- **log_count**: Total number of logs in the window
- **unique_templates**: Number of distinct Drain3 templates
- **error_count**: Number of error-level logs
- **warning_count**: Number of warning-level logs

### Template Features

- **template_frequencies**: Normalized frequency of each template ID
- **template_entropy**: Shannon entropy of template distribution (measures randomness)

### Service Features

- **service_distribution**: Count of logs per service

### Temporal Features

- **logs_per_second**: Average log rate in the window

### ML-Ready Features

- **feature_array**: Flattened numerical array suitable for scikit-learn models
- **feature_names**: Names corresponding to each element in feature_array

## Performance Considerations

### Memory Usage

- **Log buffer**: Circular buffer (default: 20,000 logs max)
- **Feature buffer**: Circular buffer (default: 1,000 features max)
- Automatically discards oldest entries when full

### CPU Usage

- Feature extraction runs every 10 seconds (configurable)
- Extraction is I/O-bound (no heavy computation)
- Shannon entropy computation is O(n) where n = unique templates

### Throughput

- Tested with 10,000+ logs/minute
- Window extraction is O(n) where n = buffer size
- Feature extraction is O(m) where m = logs per window

## Future Enhancements

### Planned Features

1. **Feature persistence**: Store feature vectors in PostgreSQL
2. **Template embeddings**: Semantic similarity using word2vec/BERT
3. **Time series features**: Rolling statistics, lag features
4. **Service topology features**: Inter-service communication patterns
5. **Anomaly scoring**: Real-time anomaly detection on features

### Extension Points

```python
# Custom feature extraction
class CustomFeatureExtractor(SlidingWindowExtractor):
    def extract_features(self, window: LogWindow) -> FeatureVector:
        base_features = super().extract_features(window)
        
        # Add custom features
        base_features.feature_array.extend([
            custom_metric_1,
            custom_metric_2,
        ])
        
        return base_features
```

## Troubleshooting

### No windows generated

**Problem**: `/features/stats` shows `windows_generated: 0`

**Solutions:**
- Check `min_logs_per_window` setting (may be too high)
- Verify logs are being ingested: `/drain3/stats`
- Check `buffer_size` in extractor stats
- Reduce `window_size_seconds` for faster testing

### High memory usage

**Problem**: Backend consuming excessive memory

**Solutions:**
- Reduce `max_logs_per_window` in WindowConfig
- Decrease feature_buffer_size in FeatureExtractionWorker
- Increase extraction_interval_seconds to process windows more frequently

### Missing features

**Problem**: Feature vectors have zero values

**Solutions:**
- Check that windows contain logs: inspect `/features/recent`
- Verify log timestamps are in the correct timezone (UTC)
- Ensure `min_logs_per_window` is not filtering all windows

## References

- [Drain3 Documentation](https://github.com/logpai/Drain3)
- [Shannon Entropy](https://en.wikipedia.org/wiki/Entropy_(information_theory))
- [Sliding Window Algorithm](https://en.wikipedia.org/wiki/Sliding_window_protocol)

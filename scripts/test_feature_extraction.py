"""Test script for sliding window feature extraction.

This script validates all prerequisites and tests the feature extraction pipeline.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.ml.feature_extraction import SlidingWindowExtractor, WindowConfig
from backend.app.models import ParsedLog


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def test_parsed_log_model() -> None:
    """Test 1: Validate ParsedLog Pydantic model."""
    print_section("Test 1: ParsedLog Pydantic Model")
    
    # Create a sample parsed log
    log = ParsedLog(
        timestamp=datetime.now(timezone.utc),
        service="auth-service",
        level="info",
        raw_message="user user-123 logged in from 192.168.1.100",
        template_id="cluster-1",
        template_text="user <*> logged in from <*>",
        parameters=[
            {"value": "user-123", "mask_name": "user"},
            {"value": "192.168.1.100", "mask_name": "ip"},
        ],
        cluster_size=15,
        change_type="none",
        correlation_id="trace-001",
    )
    
    print("✓ ParsedLog model created successfully")
    print(f"  - Service: {log.service}")
    print(f"  - Template ID: {log.template_id}")
    print(f"  - Parameters: {len(log.parameters)}")
    print(f"  - Timestamp: {log.timestamp.isoformat()}")
    
    # Test serialization
    log_dict = log.model_dump(mode="json")
    print(f"\n✓ Serialization successful: {len(log_dict)} fields")
    
    # Test validation
    try:
        invalid_log = ParsedLog(
            timestamp=datetime.now(timezone.utc),
            service="",  # Invalid: empty string
            level="info",
            raw_message="test",
            template_id="cluster-1",
        )
        print("✗ Validation failed: should reject empty service")
    except Exception as e:
        print(f"✓ Validation working: {type(e).__name__}")


def test_window_config() -> None:
    """Test 2: Validate WindowConfig model."""
    print_section("Test 2: WindowConfig Model")
    
    config = WindowConfig(
        window_size_seconds=60,
        stride_seconds=30,
        min_logs_per_window=5,
        max_logs_per_window=1000,
    )
    
    print("✓ WindowConfig created successfully")
    print(f"  - Window size: {config.window_size_seconds}s")
    print(f"  - Stride: {config.stride_seconds}s")
    print(f"  - Min logs: {config.min_logs_per_window}")
    print(f"  - Max logs: {config.max_logs_per_window}")
    
    config.validate_config()
    print("✓ Configuration validation passed")


def test_sliding_window_extractor() -> None:
    """Test 3: Test sliding window extraction logic."""
    print_section("Test 3: Sliding Window Extraction")
    
    config = WindowConfig(
        window_size_seconds=60,
        stride_seconds=30,
        min_logs_per_window=2,
    )
    
    extractor = SlidingWindowExtractor(config)
    print(f"✓ Extractor initialized with {config.window_size_seconds}s windows")
    
    # Create synthetic logs over a 3-minute period
    base_time = datetime.now(timezone.utc)
    synthetic_logs = []
    
    for i in range(30):
        log_time = base_time + timedelta(seconds=i * 6)  # One log every 6 seconds
        log = ParsedLog(
            timestamp=log_time,
            service=f"service-{i % 3}",
            level="info" if i % 5 != 0 else "error",
            raw_message=f"Log message {i}",
            template_id=f"cluster-{i % 5}",
            template_text="Log message <*>",
            parameters=[{"value": str(i), "mask_name": "number"}],
        )
        synthetic_logs.append(log)
    
    print(f"✓ Generated {len(synthetic_logs)} synthetic logs over 180s")
    
    # Add logs to extractor
    extractor.add_logs(synthetic_logs)
    print(f"✓ Added logs to extractor buffer: {len(extractor._log_buffer)} logs")
    
    # Extract windows
    current_time = base_time + timedelta(seconds=180)
    windows = extractor.get_pending_windows(current_time)
    
    print(f"✓ Extracted {len(windows)} windows")
    
    for idx, window in enumerate(windows):
        print(f"  Window {idx + 1}:")
        print(f"    - ID: {window.window_id}")
        print(f"    - Start: {window.start_time.strftime('%H:%M:%S')}")
        print(f"    - End: {window.end_time.strftime('%H:%M:%S')}")
        print(f"    - Logs: {window.log_count()}")
        print(f"    - Duration: {window.duration_seconds()}s")
        print(f"    - Templates: {len(window.template_distribution())}")


def test_feature_extraction() -> None:
    """Test 4: Test feature vector extraction."""
    print_section("Test 4: Feature Vector Extraction")
    
    config = WindowConfig(
        window_size_seconds=60,
        stride_seconds=30,
        min_logs_per_window=1,
    )
    
    extractor = SlidingWindowExtractor(config)
    
    # Create diverse logs
    base_time = datetime.now(timezone.utc)
    logs = []
    
    for i in range(20):
        log = ParsedLog(
            timestamp=base_time + timedelta(seconds=i * 2),
            service=f"service-{i % 2}",
            level=["info", "warning", "error"][i % 3],
            raw_message=f"Message {i}",
            template_id=f"template-{i % 4}",
            template_text=f"Message <*>",
            parameters=[{"value": str(i), "mask_name": "id"}],
        )
        logs.append(log)
    
    extractor.add_logs(logs)
    windows = extractor.get_pending_windows(base_time + timedelta(seconds=60))
    
    print(f"✓ Created {len(windows)} windows from {len(logs)} logs")
    
    for idx, window in enumerate(windows):
        features = extractor.extract_features(window)
        
        print(f"\n  Window {idx + 1} Features:")
        print(f"    - Log count: {features.log_count}")
        print(f"    - Unique templates: {features.unique_templates}")
        print(f"    - Error count: {features.error_count}")
        print(f"    - Warning count: {features.warning_count}")
        print(f"    - Template entropy: {features.template_entropy:.3f}")
        print(f"    - Logs per second: {features.logs_per_second:.2f}")
        print(f"    - Services: {len(features.service_distribution)}")
        print(f"    - Feature array length: {len(features.feature_array) if features.feature_array else 0}")
        
        # Validate feature array
        if features.feature_array and features.feature_names:
            assert len(features.feature_array) == len(features.feature_names)
            print(f"    ✓ Feature array validated: {features.feature_names}")


def test_statistics() -> None:
    """Test 5: Test extractor statistics."""
    print_section("Test 5: Extractor Statistics")
    
    config = WindowConfig(window_size_seconds=30, stride_seconds=15)
    extractor = SlidingWindowExtractor(config)
    
    base_time = datetime.now(timezone.utc)
    for i in range(10):
        log = ParsedLog(
            timestamp=base_time + timedelta(seconds=i),
            service="test-service",
            level="info",
            raw_message=f"Test {i}",
            template_id="template-1",
        )
        extractor.add_log(log)
    
    stats = extractor.get_stats()
    
    print("✓ Extractor statistics:")
    print(json.dumps(stats, indent=2, default=str))


def main() -> int:
    """Run all prerequisite tests."""
    print("\n" + "=" * 70)
    print("  LogSentinel Feature Extraction Prerequisites Test")
    print("=" * 70)
    
    try:
        test_parsed_log_model()
        test_window_config()
        test_sliding_window_extractor()
        test_feature_extraction()
        test_statistics()
        
        print_section("All Tests Passed ✓")
        print("Prerequisites are ready for production feature extraction.")
        return 0
    
    except Exception as e:
        print_section(f"Test Failed ✗")
        print(f"Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

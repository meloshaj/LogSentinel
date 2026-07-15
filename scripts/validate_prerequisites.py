"""Comprehensive validation script for feature extraction prerequisites.

This script checks all prerequisites without requiring a running server.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check_imports() -> bool:
    """Check that all required modules can be imported."""
    print("Checking imports...")
    
    required_imports = [
        ("pydantic", "Pydantic"),
        ("sqlalchemy", "SQLAlchemy"),
        ("fastapi", "FastAPI"),
        ("drain3", "Drain3"),
    ]
    
    optional_imports = [
        ("numpy", "NumPy (for ML features)"),
        ("sklearn", "scikit-learn (for ML models)"),
    ]
    
    all_ok = True
    
    for module, name in required_imports:
        try:
            __import__(module)
            print(f"  ✓ {name}")
        except ImportError:
            print(f"  ✗ {name} - REQUIRED")
            all_ok = False
    
    for module, name in optional_imports:
        try:
            __import__(module)
            print(f"  ✓ {name}")
        except ImportError:
            print(f"  ⚠ {name} - Optional (recommended for production)")
    
    return all_ok


def check_models() -> bool:
    """Check that Pydantic models are properly defined."""
    print("\nChecking Pydantic models...")
    
    try:
        from backend.app.models import ParsedLog, LogWindow, FeatureVector
        from datetime import datetime, timezone
        
        # Test ParsedLog
        log = ParsedLog(
            timestamp=datetime.now(timezone.utc),
            service="test",
            level="info",
            raw_message="test message",
            template_id="cluster-1",
        )
        assert log.service == "test"
        print("  ✓ ParsedLog")
        
        # Test LogWindow
        window = LogWindow(
            window_id="test-window",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            logs=[log],
        )
        assert window.log_count() == 1
        print("  ✓ LogWindow")
        
        # Test FeatureVector
        features = FeatureVector(
            window_id="test-window",
            timestamp=datetime.now(timezone.utc),
            log_count=1,
            unique_templates=1,
        )
        assert features.log_count == 1
        print("  ✓ FeatureVector")
        
        return True
    
    except Exception as e:
        print(f"  ✗ Model validation failed: {e}")
        return False


def check_ml_module() -> bool:
    """Check that ML module is properly structured."""
    print("\nChecking ML module...")
    
    try:
        from backend.app.ml import SlidingWindowExtractor, WindowConfig
        
        config = WindowConfig(window_size_seconds=60, stride_seconds=30)
        assert config.window_size_seconds == 60
        print("  ✓ WindowConfig")
        
        extractor = SlidingWindowExtractor(config)
        assert extractor.config.window_size_seconds == 60
        print("  ✓ SlidingWindowExtractor")
        
        return True
    
    except Exception as e:
        print(f"  ✗ ML module check failed: {e}")
        return False


def check_workers() -> bool:
    """Check that workers are properly defined."""
    print("\nChecking workers...")
    
    try:
        from backend.app.workers.drain_worker import DrainWorker
        from backend.app.workers.feature_worker import FeatureExtractionWorker
        
        print("  ✓ DrainWorker")
        print("  ✓ FeatureExtractionWorker")
        
        return True
    
    except Exception as e:
        print(f"  ✗ Worker check failed: {e}")
        return False


def check_integration() -> bool:
    """Check that integration points are properly wired."""
    print("\nChecking integration...")
    
    try:
        from backend.app.services.drain_parser import DrainParser
        from backend.app.models import ParsedLog
        from datetime import datetime, timezone
        
        # Check that DrainParser returns ParsedLog
        parser = DrainParser()
        result = parser.parse("test message", metadata={"service": "test"})
        
        if not isinstance(result, ParsedLog):
            print(f"  ✗ DrainParser.parse() should return ParsedLog, got {type(result)}")
            return False
        
        print("  ✓ DrainParser returns ParsedLog")
        
        # Check that callback signature is correct
        from backend.app.workers.drain_worker import DrainWorker
        import inspect
        
        sig = inspect.signature(DrainWorker.__init__)
        params = sig.parameters
        
        if "on_log_parsed" not in params:
            print("  ✗ DrainWorker missing on_log_parsed parameter")
            return False
        
        print("  ✓ DrainWorker has callback support")
        
        return True
    
    except Exception as e:
        print(f"  ✗ Integration check failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_files() -> bool:
    """Check that all required files exist."""
    print("\nChecking file structure...")
    
    required_files = [
        "backend/app/models.py",
        "backend/app/ml/__init__.py",
        "backend/app/ml/feature_extraction.py",
        "backend/app/workers/feature_worker.py",
        "backend/requirements.txt",
        "docs/FEATURE_EXTRACTION.md",
        "docs/QUICK_START_FEATURES.md",
        "scripts/test_feature_extraction.py",
    ]
    
    all_exist = True
    
    for file_path in required_files:
        full_path = ROOT / file_path
        if full_path.exists():
            print(f"  ✓ {file_path}")
        else:
            print(f"  ✗ {file_path} - MISSING")
            all_exist = False
    
    return all_exist


def check_requirements() -> bool:
    """Check that requirements.txt has ML dependencies."""
    print("\nChecking requirements.txt...")
    
    try:
        requirements_path = ROOT / "backend" / "requirements.txt"
        content = requirements_path.read_text()
        
        required_deps = ["numpy", "scikit-learn"]
        all_found = True
        
        for dep in required_deps:
            if dep in content:
                print(f"  ✓ {dep}")
            else:
                print(f"  ⚠ {dep} - Not found in requirements.txt")
                all_found = False
        
        return all_found
    
    except Exception as e:
        print(f"  ✗ Failed to check requirements.txt: {e}")
        return False


def check_api_endpoints() -> bool:
    """Check that API endpoints are defined in main.py."""
    print("\nChecking API endpoints...")
    
    try:
        main_path = ROOT / "backend" / "app" / "main.py"
        content = main_path.read_text()
        
        required_endpoints = [
            ("/features/stats", "GET /features/stats"),
            ("/features/recent", "GET /features/recent"),
            ("/features/extract", "POST /features/extract"),
        ]
        
        all_found = True
        
        for endpoint, description in required_endpoints:
            if endpoint in content:
                print(f"  ✓ {description}")
            else:
                print(f"  ✗ {description} - Not found")
                all_found = False
        
        return all_found
    
    except Exception as e:
        print(f"  ✗ Failed to check API endpoints: {e}")
        return False


def main() -> int:
    """Run all prerequisite checks."""
    print("=" * 70)
    print("  LogSentinel Feature Extraction - Prerequisites Validation")
    print("=" * 70)
    
    checks = [
        ("Import Check", check_imports),
        ("Pydantic Models", check_models),
        ("ML Module", check_ml_module),
        ("Workers", check_workers),
        ("Integration", check_integration),
        ("File Structure", check_files),
        ("Requirements", check_requirements),
        ("API Endpoints", check_api_endpoints),
    ]
    
    results = []
    
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name} failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    print("\n" + "=" * 70)
    print("  Summary")
    print("=" * 70)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status:8} {name}")
    
    all_passed = all(result for _, result in results)
    
    if all_passed:
        print("\n" + "=" * 70)
        print("  ✓ All prerequisites validated successfully!")
        print("=" * 70)
        print("\nThe codebase is ready for feature extraction.")
        print("\nNext steps:")
        print("  1. Run tests: python scripts/test_feature_extraction.py")
        print("  2. Start backend: cd backend && uvicorn app.main:app --reload")
        print("  3. Send logs: python scripts/demo_drain3_e2e.py")
        print("  4. Check features: curl http://localhost:8000/features/recent")
        return 0
    else:
        print("\n" + "=" * 70)
        print("  ✗ Some prerequisites failed validation")
        print("=" * 70)
        print("\nPlease review the errors above and fix them before proceeding.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

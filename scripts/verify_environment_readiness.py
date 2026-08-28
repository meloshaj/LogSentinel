#!/usr/bin/env python3
"""
Verify environment readiness for LogSentinel production deployment.
"""

import base64
import os
import subprocess
import sys
from pathlib import Path

def print_step(msg):
    print(f"\n\033[1;34m[RUNNING]\033[0m {msg}...")

def print_pass(msg):
    print(f"\033[1;32m[PASS]\033[0m {msg}")

def print_fail(msg):
    print(f"\033[1;31m[FAIL]\033[0m {msg}")
    sys.exit(1)

def run_cmd(cmd, cwd=None, env=None):
    try:
        result = subprocess.run(cmd, cwd=cwd, env=env, shell=True, check=True, capture_output=True, text=True)
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr or e.stdout

def verify_secrets():
    print_step("Validating Secret Integrity")
    env_file = Path(".env.prod")
    if not env_file.exists():
        print_fail(".env.prod not found. Please run generate_production_env.py first.")
    
    encryption_key = None
    jwt_secret = None
    
    with open(env_file, "r") as f:
        for line in f:
            if line.startswith("ENCRYPTION_KEY="):
                encryption_key = line.strip().split("=", 1)[1]
            elif line.startswith("JWT_SECRET_KEY="):
                jwt_secret = line.strip().split("=", 1)[1]
                
    if not encryption_key:
        print_fail("ENCRYPTION_KEY missing in .env.prod")
    if not jwt_secret:
        print_fail("JWT_SECRET_KEY missing in .env.prod")
        
    try:
        decoded = base64.urlsafe_b64decode(encryption_key + '==')
        if len(decoded) < 32:
            print_fail("ENCRYPTION_KEY must be a valid 32-byte base64 string.")
    except Exception:
        print_fail("ENCRYPTION_KEY is not a valid base64 string.")
        
    if len(jwt_secret) < 32:
        print_fail("JWT_SECRET_KEY does not meet minimum entropy requirements (>= 32 chars).")
        
    print_pass("Secrets validated successfully.")

def verify_frontend():
    print_step("Verifying Frontend Build")
    success, out = run_cmd("npm run build")
    if not success:
        print_fail(f"Frontend build failed:\n{out}")
        
    dist_dir = Path("dist")
    if not (dist_dir / "index.html").exists():
        print_fail("dist/index.html not found after build.")
    if not (dist_dir / "_redirects").exists():
        print_fail("dist/_redirects not found after build.")
        
    print_pass("Frontend built successfully and artifacts exist.")

def verify_compose():
    print_step("Verifying docker-compose.prod.yml Config")
    success, out = run_cmd("docker compose -f docker-compose.prod.yml --env-file .env.prod config")
    if not success:
        print_fail(f"Compose config validation failed:\n{out}")
    print_pass("Compose configuration is valid.")

def verify_backend_tests():
    print_step("Verifying Backend Tests")
    # Make sure we use a dummy env or the current env
    env = os.environ.copy()
    env["ENVIRONMENT"] = "test"
    env["POSTGRES_PASSWORD"] = "dummy"
    env["JWT_SECRET_KEY"] = "dummy_secret_for_testing"
    env["ENCRYPTION_KEY"] = base64.urlsafe_b64encode(b"0" * 32).decode()
    env["INGEST_API_KEY"] = "dummy"
    
    # We will try to run pytest via python module to ensure it uses the local environment
    success, out = run_cmd("pytest backend/tests", env=env)
    if not success:
        print_fail(f"Backend tests failed:\n{out}")
    print_pass("Backend tests passed successfully.")

def main():
    print("=" * 50)
    print("LogSentinel Readiness Verification")
    print("=" * 50)
    
    verify_secrets()
    verify_frontend()
    verify_compose()
    verify_backend_tests()
    
    print("\n" + "=" * 50)
    print("\033[1;32m[READY FOR DEPLOYMENT]\033[0m All systems go.")
    print("=" * 50)

if __name__ == "__main__":
    main()

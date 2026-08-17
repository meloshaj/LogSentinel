import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import aiohttp
import asyncpg
import redis.asyncio as redis

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import websockets
from backend.app.core import get_database_settings

# ANSI colors for output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# Configuration
API_URL = "http://localhost:8000/api/v1"
WS_URL = "ws://localhost:8000/ws/telemetry"
API_KEY = "ls_live_demo_key_123"
POSTGRES_DSN = get_database_settings().url.replace("+asyncpg", "").split("?")[0]
REDIS_URL = "redis://localhost:6379/0"

results = []

def record_result(name: str, status: bool, details: str = ""):
    results.append({"name": name, "status": status, "details": details})
    symbol = f"{Colors.OKGREEN}[PASS]{Colors.ENDC}" if status else f"{Colors.FAIL}[FAIL]{Colors.ENDC}"
    print(f"{symbol} {name:<40} {details}")

async def check_postgres():
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        # Check if logs table exists
        val = await conn.fetchval("SELECT to_regclass('public.logs')")
        await conn.close()
        if val:
            record_result("PostgreSQL Connectivity & Schema", True, "Connected & 'logs' table exists")
        else:
            record_result("PostgreSQL Connectivity & Schema", False, "'logs' table not found")
    except Exception as e:
        record_result("PostgreSQL Connectivity & Schema", False, str(e))

async def check_redis():
    try:
        client = redis.from_url(REDIS_URL)
        ping = await client.ping()
        
        # Check stream status
        stream_info = None
        try:
            stream_info = await client.xinfo_stream("logs:stream")
        except Exception:
            pass # Stream might not exist yet
            
        await client.aclose()
        if ping:
            details = f"Ping OK. Stream length: {stream_info.get('length', 0) if stream_info else 'Not created'}"
            record_result("Valkey/Redis Connectivity", True, details)
        else:
            record_result("Valkey/Redis Connectivity", False, "Ping failed")
    except Exception as e:
        record_result("Valkey/Redis Connectivity", False, str(e))

async def check_api():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{API_URL}/topology") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "nodes" in data and "edges" in data:
                        record_result("FastAPI Topology Endpoint", True, f"HTTP 200, {len(data.get('nodes', []))} nodes")
                    else:
                        record_result("FastAPI Topology Endpoint", False, "Invalid schema")
                else:
                    record_result("FastAPI Topology Endpoint", False, f"HTTP {resp.status}")
    except Exception as e:
        record_result("FastAPI Topology Endpoint", False, str(e))

def run_pytest():
    try:
        print(f"\n{Colors.OKCYAN}Running Pytest Regression Suite...{Colors.ENDC}")
        # Run pytest via subprocess. Run from root directory
        # Since script is in scripts/, we assume CWD is project root
        result = subprocess.run(
            ["pytest", "tests/test_e2e_pipeline.py", "-v"],
            capture_output=True,
            text=True
        )
        
        output = result.stdout
        if result.returncode == 0:
            # Try to parse pass count
            summary = output.splitlines()[-1] if output else "Passed"
            record_result("Pytest Regression Suite", True, summary)
        else:
            record_result("Pytest Regression Suite", False, f"Exit code {result.returncode}")
    except Exception as e:
        record_result("Pytest Regression Suite", False, str(e))

def run_npm_build():
    try:
        print(f"\n{Colors.OKCYAN}Running NPM Build (Frontend)...{Colors.ENDC}")
        result = subprocess.run(
            ["npm", "run", "build"],
            capture_output=True,
            text=True,
            shell=True # Needed for npm on Windows
        )
        if result.returncode == 0:
            record_result("Frontend Build (TypeScript/Bundling)", True, "Zero errors")
        else:
            record_result("Frontend Build (TypeScript/Bundling)", False, f"Exit code {result.returncode}")
    except Exception as e:
        record_result("Frontend Build (TypeScript/Bundling)", False, str(e))

async def test_e2e_roundtrip():
    print(f"\n{Colors.OKCYAN}Running E2E Live Round-Trip Verification...{Colors.ENDC}")
    probe_id = str(uuid.uuid4())
    
    payload = {
        "source": "smoke-test",
        "logs": []
    }
    for i in range(5):
        payload["logs"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service_name": "smoke-test-service",
            "level": "INFO",
            "message": f"Smoke test log {i}",
            "trace_id": probe_id,
            "metadata": {"probe_id": probe_id}
        })
        
    ws_received = False
    
    async def listen_ws():
        nonlocal ws_received
        try:
            async with websockets.connect(WS_URL) as ws:
                start = time.time()
                while time.time() - start < 5.0:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(message)
                        if data.get("type") == "frame_update":
                            for event in data.get("payload", {}).get("events", []):
                                if event.get("type") == "log.parsed":
                                    log_data = event.get("payload", {}).get("log", {})
                                    if log_data and log_data.get("trace_id") == probe_id:
                                        ws_received = True
                                        return
                    except asyncio.TimeoutError:
                        continue
        except Exception as e:
            print(f"WS error: {e}")

    async def send_http():
        await asyncio.sleep(1.0) # wait for WS to connect
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
                async with session.post(f"{API_URL}/ingest/bulk", json=payload, headers=headers) as resp:
                    if resp.status not in (200, 202):
                        print(f"HTTP ingest failed: {resp.status}")
        except Exception as e:
            print(f"HTTP error: {e}")

    await asyncio.gather(
        listen_ws(),
        send_http()
    )
    
    if ws_received:
        record_result("E2E WebSocket Round-Trip", True, "Successfully received log.parsed via WS")
    else:
        record_result("E2E WebSocket Round-Trip", False, "Timeout waiting for log.parsed on WS")

def print_summary():
    print(f"\n{Colors.BOLD}{Colors.HEADER}=================================================={Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}          LOGSENTINEL SMOKE TEST SUMMARY          {Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}=================================================={Colors.ENDC}")
    
    all_passed = True
    for res in results:
        status_text = f"{Colors.OKGREEN}PASS{Colors.ENDC}" if res["status"] else f"{Colors.FAIL}FAIL{Colors.ENDC}"
        print(f"| {status_text:<13} | {res['name']:<35} |")
        if not res["status"]:
            all_passed = False
            
    print(f"{Colors.BOLD}{Colors.HEADER}=================================================={Colors.ENDC}")
    
    if all_passed:
        print(f"\n{Colors.BOLD}{Colors.OKGREEN}[SUCCESS] ALL SYSTEMS GO! LogSentinel is ready for flight.{Colors.ENDC}\n")
        sys.exit(0)
    else:
        print(f"\n{Colors.BOLD}{Colors.FAIL}[ERROR] PRE-FLIGHT CHECKS FAILED. See details above.{Colors.ENDC}\n")
        sys.exit(1)

async def main():
    print(f"{Colors.BOLD}{Colors.OKBLUE}Initiating LogSentinel Automated Pre-Flight Checks...{Colors.ENDC}\n")
    
    print(f"{Colors.OKCYAN}Running Infrastructure Health Checks...{Colors.ENDC}")
    await check_postgres()
    await check_redis()
    await check_api()
    
    # Run synchronous subprocess tests
    # We use a thread executor so we don't block the event loop if we want to run them concurrently, 
    # but sequential is fine for a smoke test.
    await asyncio.to_thread(run_pytest)
    await asyncio.to_thread(run_npm_build)
    
    # E2E Round Trip
    await test_e2e_roundtrip()
    
    # Summary
    print_summary()

if __name__ == "__main__":
    # Workaround for ProactorEventLoop on Windows throwing errors on exit
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

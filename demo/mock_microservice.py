import os
import json
import uuid
import asyncio
import random
from datetime import datetime, timezone
from fastapi import FastAPI
from contextlib import asynccontextmanager

SERVICE_NAME = os.environ.get("SERVICE_NAME", "unknown-service")
LOG_DIR = "/var/log/mock-services"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")

os.makedirs(LOG_DIR, exist_ok=True)

app_state = {
    "fault_type": None,
    "is_degraded": False
}

def generate_log(level, message, **kwargs):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service_name": SERVICE_NAME,
        "level": level,
        "message": message,
        "trace_id": str(uuid.uuid4()),
        "span_id": str(uuid.uuid4())[:16],
        "metadata": kwargs
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

async def log_generator():
    generate_log("INFO", f"Started {SERVICE_NAME} mock service")
    while True:
        try:
            if not app_state["is_degraded"]:
                if SERVICE_NAME == "auth-service":
                    msgs = ["JWT token verified successfully", "User login successful", "Token refresh request"]
                elif SERVICE_NAME == "order-service":
                    msgs = ["Cart created", "Inventory validated for checkout", "Order processed"]
                elif SERVICE_NAME == "payment-gateway":
                    msgs = ["Charge authorization hold placed", "Stripe webhook received", "Ledger entry recorded"]
                else:
                    msgs = ["Processing request", "Heartbeat ok"]
                
                generate_log("INFO", random.choice(msgs), latency_ms=random.randint(5, 50))
                await asyncio.sleep(random.uniform(0.5, 2.0))
            else:
                fault = app_state["fault_type"]
                if fault == "db_pool_exhaustion":
                    generate_log("CRITICAL", "Database connection pool exhausted! Unable to acquire connection.", error_code="DB_POOL_001")
                    await asyncio.sleep(0.5)
                elif fault == "downstream_timeout":
                    generate_log("ERROR", "Downstream service timeout after 5000ms", downstream="payment-gateway", status=504)
                    await asyncio.sleep(0.5)
                elif fault == "gateway_timeout":
                    generate_log("ERROR", "504 Gateway Timeout: Upstream service unavailable", status=504, path="/api/login")
                    await asyncio.sleep(0.5)
                else:
                    generate_log("ERROR", f"Unknown fault state: {fault}")
                    await asyncio.sleep(1.0)
        except Exception as e:
            print(f"Error in generator: {e}")
            await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(log_generator())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

@app.post("/fault/inject")
async def inject_fault(type: str):
    app_state["fault_type"] = type
    app_state["is_degraded"] = True
    generate_log("WARN", f"Manual fault injection triggered: {type}")
    return {"status": "fault_injected", "type": type}

@app.post("/fault/clear")
async def clear_fault():
    app_state["fault_type"] = None
    app_state["is_degraded"] = False
    generate_log("INFO", "Fault state cleared. System recovering.")
    return {"status": "fault_cleared"}

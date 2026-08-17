#!/usr/bin/env python3
"""Realistic Microservice Incident & Synthetic Traffic Generator for LogSentinel.

Simulates a multi-tier microservice architecture:
    api-gateway -> auth-service -> order-service -> payment-gateway -> postgres-db

Two Execution Phases:
    1. Phase A (Steady State): Normal baseline traffic with low latency, successful
       authentications, order creation, payment settlements, and fast database queries.
    2. Phase B (Cascading Incident): Simulates PostgreSQL connection pool exhaustion,
       which cascades upstream causing query timeouts in payment-gateway, circuit
       breaker trips in order-service, and HTTP 504 Gateway Timeouts at api-gateway.

Usage:
    python scripts/simulate_incident.py --url http://localhost:8000 --api-key dev-local-key
    python scripts/simulate_incident.py --rate 10 --steady-duration 20 --incident-duration 25
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

try:
    import httpx
except ImportError:
    httpx = None  # Handled with graceful fallback to standard urllib if needed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("simulate_incident")

# ---------------------------------------------------------------------------
# Constants & Topologies
# ---------------------------------------------------------------------------

SERVICES = [
    "api-gateway",
    "auth-service",
    "order-service",
    "payment-gateway",
    "postgres-db",
]

USERS = [f"user_{uuid4().hex[:8]}" for _ in range(20)]
PRODUCTS = [f"sku_prod_{random.randint(100, 999)}" for _ in range(15)]
IP_ADDRESSES = [
    f"192.168.1.{random.randint(10, 250)}" for _ in range(10)
] + [f"10.0.4.{random.randint(2, 254)}" for _ in range(10)]


@dataclass
class SimulationStats:
    total_requests: int = 0
    total_logs_sent: int = 0
    steady_state_logs: int = 0
    incident_logs: int = 0
    successful_deliveries: int = 0
    failed_deliveries: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    @property
    def duration(self) -> float:
        end = self.end_time or time.time()
        return max(0.001, end - self.start_time)

    @property
    def throughput_logs_per_sec(self) -> float:
        return self.total_logs_sent / self.duration


# ---------------------------------------------------------------------------
# Synthetic Log Generator
# ---------------------------------------------------------------------------

class IncidentTrafficGenerator:
    """Generates synthetic distributed trace logs modeling normal vs cascading failure states."""

    def __init__(self, target_url: str, api_key: str):
        self.target_url = target_url.rstrip("/")
        self.ingest_endpoint = f"{self.target_url}/ingest-log"
        self.api_key = api_key
        self.stats = SimulationStats()

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def generate_trace_id(self) -> str:
        return f"trace-{uuid4().hex[:16]}"

    def generate_span_id(self) -> str:
        return f"span-{uuid4().hex[:8]}"

    def build_steady_state_trace(self) -> List[Dict[str, Any]]:
        """Simulate a successful end-to-end e-commerce order transaction."""
        trace_id = self.generate_trace_id()
        user_id = random.choice(USERS)
        sku = random.choice(PRODUCTS)
        client_ip = random.choice(IP_ADDRESSES)
        order_id = f"ord-{random.randint(10000, 99999)}"
        amount = round(random.uniform(15.0, 450.0), 2)
        base_time = self._utc_now()

        span_gw = self.generate_span_id()
        span_auth = self.generate_span_id()
        span_order = self.generate_span_id()
        span_pay = self.generate_span_id()
        span_db = self.generate_span_id()

        logs = [
            # 1. API Gateway receives ingress request
            {
                "service_name": "api-gateway",
                "level": "INFO",
                "message": f"POST /api/v1/orders HTTP/1.1 from {client_ip} status 200 user={user_id}",
                "timestamp": base_time,
                "metadata": {
                    "trace_id": trace_id,
                    "span_id": span_gw,
                    "parent_span_id": None,
                    "service": "api-gateway",
                    "http_status": 200,
                    "duration_ms": random.randint(15, 35),
                },
            },
            # 2. Auth Service verifies JWT
            {
                "service_name": "auth-service",
                "level": "INFO",
                "message": f"Token validated for subject {user_id} scope=orders.create issuer=logsentinel-auth",
                "timestamp": base_time,
                "metadata": {
                    "trace_id": trace_id,
                    "span_id": span_auth,
                    "parent_span_id": span_gw,
                    "service": "auth-service",
                    "user_id": user_id,
                    "duration_ms": random.randint(2, 8),
                },
            },
            # 3. Order Service creates order record
            {
                "service_name": "order-service",
                "level": "INFO",
                "message": f"Order {order_id} created for sku={sku} quantity=1 amount=${amount}",
                "timestamp": base_time,
                "metadata": {
                    "trace_id": trace_id,
                    "span_id": span_order,
                    "parent_span_id": span_gw,
                    "service": "order-service",
                    "order_id": order_id,
                    "amount": amount,
                },
            },
            # 4. Payment Gateway authorizes transaction
            {
                "service_name": "payment-gateway",
                "level": "INFO",
                "message": f"Payment captured tx_id=txn_{uuid4().hex[:8]} order_id={order_id} status=AUTHORIZED amount=${amount}",
                "timestamp": base_time,
                "metadata": {
                    "trace_id": trace_id,
                    "span_id": span_pay,
                    "parent_span_id": span_order,
                    "service": "payment-gateway",
                    "order_id": order_id,
                    "amount": amount,
                },
            },
            # 5. Database commits transaction
            {
                "service_name": "postgres-db",
                "level": "INFO",
                "message": f"COMMIT order transaction {order_id}; duration=4.2ms locks_acquired=2 pool_available=88/100",
                "timestamp": base_time,
                "metadata": {
                    "trace_id": trace_id,
                    "span_id": span_db,
                    "parent_span_id": span_pay,
                    "service": "postgres-db",
                    "order_id": order_id,
                    "pool_available": 88,
                },
            },
        ]
        return logs

    def build_cascading_incident_trace(self) -> List[Dict[str, Any]]:
        """Simulate DB pool exhaustion cascading upstream to order service and API gateway."""
        trace_id = self.generate_trace_id()
        user_id = random.choice(USERS)
        sku = random.choice(PRODUCTS)
        client_ip = random.choice(IP_ADDRESSES)
        order_id = f"ord-fail-{random.randint(10000, 99999)}"
        base_time = self._utc_now()

        span_gw = self.generate_span_id()
        span_order = self.generate_span_id()
        span_pay = self.generate_span_id()
        span_db = self.generate_span_id()

        logs = [
            # 1. Root Cause: Postgres DB connection pool exhausted
            {
                "service_name": "postgres-db",
                "level": "CRITICAL",
                "message": (
                    f"FATAL: connection pool exhausted (active=100/100, queued=487). "
                    f"Remaining connection slots are reserved for non-replication superusers. "
                    f"Transaction aborted for query: SELECT * FROM orders FOR UPDATE"
                ),
                "timestamp": base_time,
                "metadata": {
                    "trace_id": trace_id,
                    "span_id": span_db,
                    "parent_span_id": span_pay,
                    "service": "postgres-db",
                    "error_code": "53300",
                    "pool_active": 100,
                    "pool_queued": 487,
                },
            },
            # 2. Payment Gateway times out trying to acquire DB connection
            {
                "service_name": "payment-gateway",
                "level": "ERROR",
                "message": (
                    f"TimeoutError: Failed to acquire database connection after 5000ms for order {order_id}. "
                    f"Downstream postgres-db pool unreachable or unresponsive."
                ),
                "timestamp": base_time,
                "metadata": {
                    "trace_id": trace_id,
                    "span_id": span_pay,
                    "parent_span_id": span_order,
                    "service": "payment-gateway",
                    "order_id": order_id,
                    "error_type": "ConnectionTimeout",
                },
            },
            # 3. Order Service catches failure and trips circuit breaker
            {
                "service_name": "order-service",
                "level": "ERROR",
                "message": (
                    f"CircuitBreakerOpenException: payment-gateway failure rate exceeded threshold (92.4% errors). "
                    f"Rolling back order {order_id} for user {user_id}. State changed to OPEN."
                ),
                "timestamp": base_time,
                "metadata": {
                    "trace_id": trace_id,
                    "span_id": span_order,
                    "parent_span_id": span_gw,
                    "service": "order-service",
                    "order_id": order_id,
                    "circuit_breaker": "OPEN",
                },
            },
            # 4. API Gateway returns HTTP 504 / 500 to client
            {
                "service_name": "api-gateway",
                "level": "ERROR",
                "message": (
                    f"POST /api/v1/orders HTTP/1.1 from {client_ip} status 504 "
                    f"GATEWAY_TIMEOUT upstream=order-service duration_ms=5120 error='Upstream service timed out'"
                ),
                "timestamp": base_time,
                "metadata": {
                    "trace_id": trace_id,
                    "span_id": span_gw,
                    "parent_span_id": None,
                    "service": "api-gateway",
                    "http_status": 504,
                    "duration_ms": 5120,
                },
            },
        ]
        return logs

    async def send_log_batch(
        self,
        client: httpx.AsyncClient,
        logs: List[Dict[str, Any]],
        phase: str,
    ) -> bool:
        """Send a batch of structured logs to the LogSentinel ingestion gateway."""
        payload = {
            "source": "simulate-incident-script",
            "environment": "production",
            "logs": logs,
        }
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
        }

        try:
            response = await client.post(
                self.ingest_endpoint,
                json=payload,
                headers=headers,
                timeout=10.0,
            )
            self.stats.total_requests += 1
            self.stats.total_logs_sent += len(logs)
            if phase == "steady":
                self.stats.steady_state_logs += len(logs)
            else:
                self.stats.incident_logs += len(logs)

            if response.status_code in (200, 202):
                self.stats.successful_deliveries += 1
                return True
            else:
                logger.warning(
                    "Ingest rejected HTTP %s: %s",
                    response.status_code,
                    response.text[:120],
                )
                self.stats.failed_deliveries += 1
                return False
        except Exception as exc:
            logger.error("Delivery error to %s: %s", self.ingest_endpoint, exc)
            self.stats.failed_deliveries += 1
            return False

    async def run_simulation(
        self,
        rate_hz: float,
        steady_duration_s: float,
        incident_duration_s: float,
    ) -> SimulationStats:
        """Execute the two-phase simulation."""
        logger.info("=" * 64)
        logger.info("LogSentinel Realistic Incident & Microservice Traffic Generator")
        logger.info("=" * 64)
        logger.info("Target Ingest URL    : %s", self.ingest_endpoint)
        logger.info("Target Rate          : %.1f batches/sec", rate_hz)
        logger.info("Phase A (Steady)     : %.1f seconds", steady_duration_s)
        logger.info("Phase B (Incident)   : %.1f seconds", incident_duration_s)
        logger.info("Topology Modeled     : %s", " -> ".join(SERVICES))
        logger.info("=" * 64)

        delay = 1.0 / max(0.1, rate_hz)

        async with httpx.AsyncClient() as client:
            # -------------------------------------------------------------------
            # Phase A: Steady State
            # -------------------------------------------------------------------
            logger.info(">>> STARTING PHASE A: Steady State Baseline Traffic...")
            end_steady = time.time() + steady_duration_s
            batch_count = 0

            while time.time() < end_steady:
                trace_logs = self.build_steady_state_trace()
                await self.send_log_batch(client, trace_logs, phase="steady")
                batch_count += 1
                if batch_count % 10 == 0:
                    logger.info(
                        "[Steady State] Sent %d batches (%d logs total)",
                        batch_count,
                        self.stats.total_logs_sent,
                    )
                await asyncio.sleep(delay)

            logger.info(
                "✓ Phase A Completed: %d baseline logs sent successfully.",
                self.stats.steady_state_logs,
            )

            # -------------------------------------------------------------------
            # Phase B: Cascading Incident Injection
            # -------------------------------------------------------------------
            logger.info(">>> INJECTING PHASE B: Cascading DB Pool Exhaustion Failure...")
            end_incident = time.time() + incident_duration_s
            incident_count = 0

            while time.time() < end_incident:
                # In phase B, emit 70% cascading failure traces and 30% degraded baseline
                if random.random() < 0.75:
                    trace_logs = self.build_cascading_incident_trace()
                else:
                    trace_logs = self.build_steady_state_trace()

                await self.send_log_batch(client, trace_logs, phase="incident")
                incident_count += 1
                if incident_count % 5 == 0:
                    logger.warning(
                        "[Incident Injection] Dispatched %d incident traces (Total logs: %d)",
                        incident_count,
                        self.stats.total_logs_sent,
                    )
                await asyncio.sleep(delay)

            logger.info("✓ Phase B Completed: Incident simulation finished.")

        self.stats.end_time = time.time()
        self.print_summary()
        return self.stats

    def print_summary(self) -> None:
        """Print a structured summary of the simulation run."""
        print("\n" + "═" * 64)
        print("  LogSentinel Simulation Run Summary")
        print("═" * 64)
        print(f"  │ Total Duration          : {self.stats.duration:.2f}s")
        print(f"  │ Total Log Events Sent   : {self.stats.total_logs_sent}")
        print(f"  │ Steady-State Logs       : {self.stats.steady_state_logs}")
        print(f"  │ Cascading Incident Logs : {self.stats.incident_logs}")
        print(f"  │ Total HTTP Requests     : {self.stats.total_requests}")
        print(f"  │ Successful Deliveries   : {self.stats.successful_deliveries}")
        print(f"  │ Failed Deliveries       : {self.stats.failed_deliveries}")
        print(f"  │ Effective Throughput    : {self.stats.throughput_logs_per_sec:.1f} logs/sec")
        print("═" * 64 + "\n")


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate realistic microservice traffic and cascading DB failure incidents."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("LOGSENTINEL_API_URL", "http://localhost:8000"),
        help="LogSentinel backend base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("INGEST_API_KEY", "dev-local-key"),
        help="LogSentinel Ingest API Key (X-API-Key)",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=5.0,
        help="Target batch dispatch rate in Hertz / batches per second (default: 5.0)",
    )
    parser.add_argument(
        "--steady-duration",
        type=float,
        default=15.0,
        help="Duration of Phase A (Steady State) in seconds (default: 15.0)",
    )
    parser.add_argument(
        "--incident-duration",
        type=float,
        default=20.0,
        help="Duration of Phase B (Cascading Incident) in seconds (default: 20.0)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if httpx is None:
        logger.error("httpx package is required. Install with: pip install httpx")
        sys.exit(1)

    generator = IncidentTrafficGenerator(target_url=args.url, api_key=args.api_key)
    try:
        asyncio.run(
            generator.run_simulation(
                rate_hz=args.rate,
                steady_duration_s=args.steady_duration,
                incident_duration_s=args.incident_duration,
            )
        )
    except KeyboardInterrupt:
        logger.info("Simulation interrupted by user.")
        generator.stats.end_time = time.time()
        generator.print_summary()


if __name__ == "__main__":
    main()

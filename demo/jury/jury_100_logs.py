#!/usr/bin/env python3
"""100-event, bounded LogSentinel jury demo using the repository's Python SDK.

Run from the LogSentinel repository root. This script produces application LOGS;
only LogSentinel itself can classify them into anomalies/incidents.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
import time
import uuid
from pathlib import Path

DEFAULT_ENDPOINT = "https://138.2.152.189.sslip.io/api/v1/ingest/bulk"
PHASE_COUNTS = (("normal", 35), ("auth_failures", 25), ("payment_outage", 25), ("recovery", 15))
SERVICES = ("auth-service", "order-service", "payment-gateway", "database-service")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send bounded, labeled demo log phases via LogSentinel's Python SDK.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Verified HTTPS LogSentinel bulk-ingestion endpoint")
    parser.add_argument("--delay", type=float, default=0.35, help="Seconds between events, default: 0.35")
    parser.add_argument(
        "--phase",
        choices=[phase for phase, _ in PHASE_COUNTS],
        help="Run one bounded phase only; payment_outage sends 16 ERROR and 9 CRITICAL logs",
    )
    parser.add_argument("--interactive", action="store_true", help="Pause before each phase so you can show the dashboard")
    parser.add_argument("--dry-run", action="store_true", help="Preview all events without sending anything")
    parser.add_argument("--drain-seconds", type=float, default=8.0, help="Wait for asynchronous SDK batches after final event")
    parser.add_argument(
        "--post-ingestion-wait-seconds",
        type=float,
        default=20.0,
        help="Pipeline observation delay after SDK flush (minimum 20 seconds)",
    )
    args = parser.parse_args()
    if (
        args.delay < 0.1
        or args.drain_seconds < 2
        or args.post_ingestion_wait_seconds < 20
        or not args.endpoint.startswith("https://")
    ):
        parser.error(
            "Use --delay >= 0.1, --drain-seconds >= 2, "
            "--post-ingestion-wait-seconds >= 20, and an HTTPS endpoint"
        )
    return args


def sdk_handler_cls():
    # Intended execution location: current LogSentinel repository root.
    sdk_dir = Path.cwd() / "sdk" / "python"
    if not (sdk_dir / "logsentinel_logger.py").is_file():
        raise SystemExit("SDK not found: run from the LogSentinel repository root (sdk/python/logsentinel_logger.py).")
    sys.path.insert(0, str(sdk_dir))
    from logsentinel_logger import LogSentinelHandler  # noqa: PLC0415
    return LogSentinelHandler


def event(phase: str, index: int, run_id: str):
    """Returns (service, level, message, metadata) with no fake anomaly/incident records."""
    trace_id = f"jury-{run_id}-{phase[:4]}-{index:03d}"
    service = SERVICES[index % len(SERVICES)]
    meta = {
        "run_id": run_id,
        "scenario": phase,
        "simulated_application": "DemoShop",
        "trace_id": trace_id,
        "span_id": f"span-{index:04d}",
        "event_id": f"{trace_id}-{uuid.uuid4().hex[:8]}",
        "demo_event": True,
    }

    if phase == "normal":
        service = SERVICES[index % len(SERVICES)]
        meta.update({"http_status": 200, "response_time_ms": 35 + index % 65, "order_id": 10000 + index})
        messages = {
            "auth-service": "DemoShop user authenticated successfully",
            "order-service": "DemoShop order processed successfully",
            "payment-gateway": "DemoShop payment authorized",
            "database-service": "DemoShop database query completed",
        }
        return service, logging.INFO, messages[service], meta

    if phase == "auth_failures":
        meta.update({"http_status": 401, "response_time_ms": 160 + index % 30,
                     "error_code": "SIMULATED_BAD_CREDENTIALS", "client_label": "demo-client"})
        return "auth-service", logging.WARNING, "DemoShop simulated repeated authentication failure", meta

    if phase == "payment_outage":
        if index % 3 == 0:
            meta.update({"http_status": 503, "response_time_ms": 3100 + index * 25,
                         "error_code": "SIMULATED_DB_UNAVAILABLE"})
            return "database-service", logging.CRITICAL, "DemoShop database dependency unavailable during checkout", meta
        meta.update({"http_status": 500 if index % 2 else 504,
                     "response_time_ms": 2200 + index * 40,
                     "error_code": "SIMULATED_PAYMENT_TIMEOUT"})
        return "payment-gateway", logging.ERROR, "DemoShop payment request failed after database timeout", meta

    # Recovery means new healthy events, NOT deletion of past anomaly/incident records.
    meta.update({"http_status": 200, "response_time_ms": 40 + index % 40,
                 "recovery_after": "simulated-payment-outage"})
    return "payment-gateway" if index % 2 else "order-service", logging.INFO, "DemoShop service recovered; request completed", meta


def main() -> int:
    args = parse_args()
    run_id = uuid.uuid4().hex[:12]
    selected_phases = tuple(
        (phase, count)
        for phase, count in PHASE_COUNTS
        if args.phase is None or phase == args.phase
    )
    expected_total = sum(count for _, count in selected_phases)

    if args.phase == "payment_outage":
        severity_counts = {
            logging.getLevelName(event(args.phase, index, run_id)[1]): 0
            for index in range(expected_total)
        }
        for index in range(expected_total):
            severity_counts[logging.getLevelName(event(args.phase, index, run_id)[1])] += 1
        if severity_counts != {"ERROR": 16, "CRITICAL": 9}:
            raise SystemExit(
                f"Payment-outage fixture changed unexpectedly: {severity_counts}"
            )

    if args.dry_run:
        loggers = {}
    else:
        handler_cls = sdk_handler_cls()
        api_key = os.getenv("LOGSENTINEL_INGEST_API_KEY") or getpass.getpass("LogSentinel REPLACEMENT ingestion key (hidden): ")
        if not api_key.strip():
            raise SystemExit("An ingestion key is required.")
        loggers = {}
        for service in SERVICES:
            logger = logging.getLogger(f"jury.{run_id}.{service}")
            logger.propagate = False
            logger.setLevel(logging.INFO)
            logger.addHandler(handler_cls(
                api_key=api_key.strip(), service_name=service, endpoint=args.endpoint,
                batch_size=5, flush_interval_seconds=1.0,
            ))
            loggers[service] = logger
        del api_key  # credentials remain internal to the SDK handler

    mode_label = "DRY RUN" if args.dry_run else "LIVE"
    print(f"\nLOGSENTINEL JURY DEMO | RUN_ID={run_id} | MODE={mode_label}")
    if args.phase:
        print(f"Selected phase only: {args.phase} ({expected_total} records).")
        if args.phase == "payment_outage":
            print("Severity counts: 16 ERROR, 9 CRITICAL.")
    else:
        print("Four phases: 35 normal, 25 simulated auth failures, 25 simulated outage, 15 recovery.")
    print("These are queued LOGS, not pre-created LogSentinel anomalies or incidents.\n")
    total = 0
    try:
        for phase, count in selected_phases:
            if args.interactive:
                input(f"Show the dashboard, then press ENTER to start '{phase}' ({count} events)... ")
            print(f"--- {phase.upper().replace('_', ' ')} / {count} generated events ---", flush=True)
            for i in range(count):
                service, level, message, meta = event(phase, i, run_id)
                if args.dry_run:
                    print(f"[PREVIEW] {service:18} {logging.getLevelName(level):8} {message}")
                else:
                    loggers[service].log(level, message, extra=meta)
                total += 1
                if (i + 1) % 5 == 0:
                    print(f"  Generated/queued in phase: {i + 1}/{count}", flush=True)
                time.sleep(args.delay)
            if not args.dry_run:
                # Permit async background transport to flush between phases.
                time.sleep(2)
        if not args.dry_run:
            print(f"Waiting {args.drain_seconds:.0f}s for asynchronous handler batches to flush...", flush=True)
            time.sleep(args.drain_seconds)
            print(
                "Waiting "
                f"{args.post_ingestion_wait_seconds:.0f}s after SDK flush "
                "for feature extraction and detection...",
                flush=True,
            )
            time.sleep(args.post_ingestion_wait_seconds)
    except KeyboardInterrupt:
        print("\nInterrupted. Waiting briefly for queued batches...", flush=True)
        if not args.dry_run:
            time.sleep(args.drain_seconds)
        return 130
    finally:
        if not args.dry_run:
            # Handler.close() behavior depends on the existing SDK implementation.
            # Allow a drain interval above; never claim delivery solely from log().
            for logger in loggers.values():
                for handler in list(logger.handlers):
                    try:
                        handler.close()
                    except Exception as exc:
                        print(f"SDK handler shutdown warning ({type(exc).__name__}).", file=sys.stderr)
                    logger.removeHandler(handler)

    print(
        f"\nFinished. Events generated/queued: {total}/{expected_total}. "
        f"Run marker: {run_id}"
    )
    if args.dry_run:
        print("Dry run: no logs were sent.")
    else:
        print("NOT a delivery receipt: verify actual acceptance, persistence, anomalies and incidents in LogSentinel.")
        print("Inspect Live Logs by run marker, then Anomalies and Incidents. No detection is guaranteed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

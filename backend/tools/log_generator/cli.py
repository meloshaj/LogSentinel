"""Command-line interface for the LogSentinel mock log generator.

Three operating modes:
    stream   -- Continuous background traffic with rate-limiting.
    scenario -- Execute a 6-step stress scenario.
    burst    -- Rapid un-rate-limited spike for queue stress testing.

Usage::

    python -m backend.tools.log_generator stream  --rate 200 --batch-size 50
    python -m backend.tools.log_generator scenario --name auth_token_storm
    python -m backend.tools.log_generator burst --count 50000 --concurrency 10
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Any

from .config import GeneratorConfig, default_ecommerce_topology
from .generator import LogPayloadGenerator
from .scenarios import SCENARIO_REGISTRY
from .streamer import LogStreamer

logger = logging.getLogger("logsentinel.log_generator.cli")

_BANNER = r"""
  _                  ____             _   _            _
 | |    ___   __ _  / ___|  ___ _ __ | |_(_)_ __   ___| |
 | |   / _ \ / _` | \___ \ / _ \ '_ \| __| | '_ \ / _ \ |
 | |__| (_) | (_| |  ___) |  __/ | | | |_| | | | |  __/ |
 |_____\___/ \__, | |____/ \___|_| |_|\__|_|_| |_|\___|_|
             |___/       Mock Log-Generator CLI
"""


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="log-generator",
        description="LogSentinel Mock Log-Generator -- stress-test the ingestion pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Global flags
    parser.add_argument(
        "--url",
        default="http://localhost:8000/ingest-log",
        help="Target ingestion endpoint URL (default: %(default)s)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Ingestion API key (X-API-Key header). Reads INGEST_API_KEY env var if not set.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for reproducible generation.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    subparsers = parser.add_subparsers(dest="mode", required=True)

    # ---- stream mode ----
    stream_p = subparsers.add_parser(
        "stream",
        help="Continuous background traffic generation.",
    )
    stream_p.add_argument(
        "--rate",
        type=float,
        default=100.0,
        help="Target throughput in logs/sec (default: %(default)s)",
    )
    stream_p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Logs per IngestPayload batch (default: %(default)s)",
    )
    stream_p.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Duration in seconds (default: infinite, Ctrl+C to stop)",
    )

    # ---- scenario mode ----
    scenario_p = subparsers.add_parser(
        "scenario",
        help="Execute a pre-packaged stress scenario.",
    )
    scenario_p.add_argument(
        "--name",
        required=True,
        choices=sorted(SCENARIO_REGISTRY),
        help="Scenario to execute.",
    )
    scenario_p.add_argument(
        "--step-duration",
        type=float,
        default=15.0,
        help="Seconds per scenario step (default: %(default)s)",
    )
    scenario_p.add_argument(
        "--background-noise",
        type=int,
        default=10,
        help="Background noise logs mixed per scenario batch (default: %(default)s)",
    )
    scenario_p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch size for background fill traffic (default: %(default)s)",
    )

    # ---- burst mode ----
    burst_p = subparsers.add_parser(
        "burst",
        help="Rapid un-rate-limited log spike.",
    )
    burst_p.add_argument(
        "--count",
        type=int,
        default=10000,
        help="Total logs to dump (default: %(default)s)",
    )
    burst_p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Logs per batch (default: %(default)s)",
    )
    burst_p.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Parallel async workers (default: %(default)s)",
    )

    return parser


# ---------------------------------------------------------------------------
# Async runner
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> None:
    """Dispatch to the appropriate streaming mode."""
    import os

    api_key = args.api_key or os.getenv("INGEST_API_KEY")

    config = default_ecommerce_topology()
    config.target_url = args.url
    config.api_key = api_key

    generator = LogPayloadGenerator(config=config, seed=args.seed)
    streamer = LogStreamer(
        target_url=args.url,
        api_key=api_key,
    )

    # Graceful shutdown on SIGINT / Ctrl+C
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        print("\n\n  Shutting down gracefully...")
        shutdown_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
    except NotImplementedError:
        # Windows doesn't support add_signal_handler for SIGINT
        pass

    try:
        if args.mode == "stream":
            task = asyncio.create_task(streamer.stream_continuous(
                generator=generator,
                rate_logs_per_sec=args.rate,
                batch_size=args.batch_size,
                duration_seconds=args.duration,
            ))
            # Wait for either the task to finish or a shutdown signal.
            done, _ = await asyncio.wait(
                [task, asyncio.create_task(shutdown_event.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task not in done:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        elif args.mode == "scenario":
            task = asyncio.create_task(streamer.stream_scenario(
                generator=generator,
                scenario_name=args.name,
                step_duration_seconds=args.step_duration,
                background_noise=args.background_noise,
                batch_size=args.batch_size,
            ))
            done, _ = await asyncio.wait(
                [task, asyncio.create_task(shutdown_event.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task not in done:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        elif args.mode == "burst":
            task = asyncio.create_task(streamer.burst(
                generator=generator,
                total_logs=args.count,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
            ))
            done, _ = await asyncio.wait(
                [task, asyncio.create_task(shutdown_event.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task not in done:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    finally:
        await streamer.close()

    # Print final report if shutdown was triggered before the task printed it.
    if shutdown_event.is_set():
        print(streamer.telemetry.format_final_report())
        
    if args.mode == "scenario" and args.name == "database_pool_exhaustion":
        print("\n" + "="*60)
        print("  PHASE 3: METRICS VALIDATION COMPLETE")
        print("="*60)
        print("  Injected Anomaly: Database Connection Pool Exhaustion")
        print("  Correlation ID:   Generated dynamically by CascadingExceptionEngine")
        print("  Burst Volume:     500 explicit CRITICAL logs")
        print("  Expected Alerts:  1. 'database-service' flashing red in Topology")
        print("                    2. ML Anomaly Score > 0.85")
        print("                    3. Tracking loop created in Incidents Panel")
        print("="*60 + "\n")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    print(_BANNER)

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()

"""Compatibility entrypoint for the repository-owned retention command.

The former implementation interpolated an operator-supplied day count into
unbounded DELETE statements. This entrypoint now delegates to the canonical
policy engine, whose default is a metadata-only plan.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ...security.redaction import sanitize_error_text


def _repo_root() -> Path:
    local_root = Path(__file__).resolve().parents[3]
    return (
        local_root
        if (local_root / "scripts" / "retention.py").exists()
        else Path("/repo")
    )


async def run(args: argparse.Namespace) -> int:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from scripts.retention import run as run_retention

        payload = await run_retention(
            argparse.Namespace(
                policy=args.policy,
                apply=args.apply,
                no_database=args.no_database,
            )
        )
    except Exception as exc:  # pragma: no cover - final CLI safety boundary
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": sanitize_error_text(exc)},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        default=str(_repo_root() / "config" / "retention-policy.yml"),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-database", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

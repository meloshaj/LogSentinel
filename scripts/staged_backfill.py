"""Explicit, bounded copy from a reviewed projection into an expanded table.

No rename/drop/cutover is performed. See remediation-01 migration runbook.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

import asyncpg


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError("only simple reviewed SQL identifiers are allowed")
    return '"' + value + '"'


def validate_contract(contract: dict) -> None:
    for field in ("source", "target"):
        identifier(contract[field])
    if contract["source"] == contract["target"]:
        raise ValueError("source and target must differ")
    for field in ("columns", "keys"):
        if not contract[field] or len(set(contract[field])) != len(contract[field]):
            raise ValueError("columns and keys must be nonempty and unique")
        for name in contract[field]:
            identifier(name)
    if not set(contract["keys"]) <= set(contract["columns"]):
        raise ValueError("keys must be copied columns")
    if not contract.get("review_reference") or not contract.get("tenant_mapping_reference"):
        raise ValueError("review and tenant mapping evidence are required")


async def run(connection, contract: dict, *, execute=False, batch_size=1000, max_batches=1):
    validate_contract(contract)
    if not 1 <= batch_size <= 10000 or not 1 <= max_batches <= 1000:
        raise ValueError("batch_size 1..10000 and max_batches 1..1000 required")
    fingerprint = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    source, target = (identifier(contract[n]) for n in ("source", "target"))
    columns = ", ".join(identifier(n) for n in contract["columns"])
    keys = ", ".join(identifier(n) for n in contract["keys"])
    # json_populate_record uses the reviewed source composite's actual types.
    cursor_keys = ", ".join("p." + identifier(n) for n in contract["keys"])
    query = f"""SELECT row_to_json(r)::text AS payload FROM
      (SELECT {columns} FROM public.{source}
       WHERE ($1::text IS NULL OR ROW({keys}) >
         (SELECT ROW({cursor_keys}) FROM json_populate_record(NULL::public.{source}, $1::json) p))
       ORDER BY {keys} LIMIT $2) r"""
    if not execute:
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL statement_timeout = '30s'")
            rows = await connection.fetch(query, None, batch_size)
        return {"dry_run": True, "contract_sha256": fingerprint, "sample_rows": len(rows),
                "max_rows_per_run": batch_size * max_batches, "writes": 0}
    await connection.execute("SELECT pg_advisory_lock(hashtext('logsentinel.staged_backfill'))")
    try:
        await connection.execute("""CREATE TABLE IF NOT EXISTS remediation_backfill_progress (
          contract_sha256 text PRIMARY KEY, cursor_json text, copied bigint NOT NULL DEFAULT 0,
          batch_sha256 text, updated_at timestamptz NOT NULL DEFAULT now())""")
        for _ in range(max_batches):
            async with connection.transaction():
                await connection.execute("SET LOCAL lock_timeout = '5s'")
                await connection.execute("SET LOCAL statement_timeout = '30s'")
                await connection.execute(
                    "INSERT INTO remediation_backfill_progress(contract_sha256) VALUES($1) ON CONFLICT DO NOTHING",
                    fingerprint)
                state = await connection.fetchrow(
                    "SELECT * FROM remediation_backfill_progress WHERE contract_sha256=$1 FOR UPDATE", fingerprint)
                rows = await connection.fetch(query, state["cursor_json"], batch_size)
                if not rows:
                    return {"dry_run": False, "contract_sha256": fingerprint,
                            "copied": state["copied"], "copy_exhausted": True,
                            "validated": False, "cutover_permitted": False}
                payloads = [r["payload"] for r in rows]
                await connection.execute(f"""INSERT INTO public.{target} ({columns})
                  SELECT {columns} FROM json_populate_recordset(NULL::public.{target}, $1::json)
                  ON CONFLICT ({keys}) DO NOTHING""", '[' + ','.join(payloads) + ']')
                # A pre-existing conflicting row must match every copied field.
                actual = await connection.fetch(f"""SELECT row_to_json(r)::text AS payload FROM
                  (SELECT {', '.join('t.' + identifier(n) for n in contract['columns'])}
                   FROM public.{target} t JOIN json_populate_recordset(NULL::public.{target}, $1::json) s
                   USING ({keys}) ORDER BY {', '.join('t.' + identifier(n) for n in contract['keys'])}) r""",
                    '[' + ','.join(payloads) + ']')
                expected_hash = hashlib.sha256('\n'.join(payloads).encode()).hexdigest()
                actual_hash = hashlib.sha256('\n'.join(r['payload'] for r in actual).encode()).hexdigest()
                if len(actual) != len(rows) or expected_hash != actual_hash:
                    raise ValueError("batch count/checksum mismatch; transaction rolled back")
                await connection.execute("""UPDATE remediation_backfill_progress SET cursor_json=$2,
                  copied=copied+$3,batch_sha256=$4,updated_at=now() WHERE contract_sha256=$1""",
                    fingerprint, payloads[-1], len(rows), expected_hash)
            print(json.dumps({"contract_sha256": fingerprint, "batch_rows": len(rows),
                              "batch_sha256": expected_hash}), flush=True)
        return {"copy_exhausted": False, "validated": False, "cutover_permitted": False}
    finally:
        await connection.execute("SELECT pg_advisory_unlock(hashtext('logsentinel.staged_backfill'))")


async def validate_copy(connection, contract: dict, batch_size=1000):
    """Full count/hash comparison, bounded memory, with both relations locked.

    Operators must stop writers/retention before validation and keep them stopped
    through their separately reviewed cutover. Timeout leaves cutover prohibited.
    """
    validate_contract(contract)
    if not 1 <= batch_size <= 10000:
        raise ValueError("batch_size must be 1..10000")
    columns = ', '.join(identifier(n) for n in contract['columns'])
    keys = ', '.join(identifier(n) for n in contract['keys'])
    results = []
    async with connection.transaction(isolation='repeatable_read'):
        await connection.execute("SET LOCAL lock_timeout = '5s'")
        await connection.execute("SET LOCAL statement_timeout = '5min'")
        await connection.execute(f"LOCK TABLE public.{identifier(contract['source'])}, public.{identifier(contract['target'])} IN SHARE MODE")
        for name in ('source', 'target'):
            digest, count = hashlib.sha256(), 0
            query = f"SELECT row_to_json(r)::text AS payload FROM (SELECT {columns} FROM public.{identifier(contract[name])} ORDER BY {keys}) r"
            async for row in connection.cursor(query, prefetch=batch_size):
                digest.update(row['payload'].encode() + b'\n')
                count += 1
            results.append({'count': count, 'sha256': digest.hexdigest()})
    if results[0] != results[1]:
        raise ValueError("full source/target count or checksum mismatch")
    return {'validated': True, 'source': results[0], 'target': results[1],
            'cutover_permitted': False, 'note': 'Separate dependency/tenant/constraint review and cutover required'}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--dsn', required=True, help='Disposable rehearsal DSN or operator-managed connection; prefer service config')
    parser.add_argument('--batch-size', type=int, default=1000)
    parser.add_argument('--max-batches', type=int, default=1)
    parser.add_argument('--writers-paused', action='store_true')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--validate-copy', action='store_true')
    args = parser.parse_args()
    if (args.execute or args.validate_copy) and not args.writers_paused:
        parser.error('explicit --writers-paused maintenance contract required')
    contract = json.loads(args.contract.read_text())
    validate_contract(contract)
    connection = await asyncpg.connect(args.dsn, command_timeout=300)
    try:
        if args.validate_copy:
            result = await validate_copy(connection, contract, args.batch_size)
        else:
            result = await run(connection, contract, execute=args.execute,
                               batch_size=args.batch_size, max_batches=args.max_batches)
        print(json.dumps(result))
    finally:
        await connection.close()


if __name__ == '__main__':
    asyncio.run(main())

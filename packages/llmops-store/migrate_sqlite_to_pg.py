"""One-time copy of the shared store from SQLite to Postgres (HA migration).

Switching to Postgres (LLMOPS_DB_URL) starts with an empty database. Run this to
carry your existing SQLite data (API keys, audit log, config versions, cost
prices, request logs, …) over.

  LLMOPS_DB_URL=postgresql://user:pass@host:5432/db \
    python packages/llmops-store/migrate_sqlite_to_pg.py --src data/llmops.db

Re-running refuses unless --wipe (which TRUNCATEs the target tables first), so you
can't accidentally double-insert. Runtime-only tables (leader_lease, draining) are
skipped — they self-rebuild. The current overlay rides along in config_versions
(its newest snapshot), so a freshly-migrated Postgres hydrates the same models.

Inside Docker (data lives in the llmops-data volume), run it in the engine image:

  docker compose -f deploy/docker-compose.yaml run --rm --no-deps \
    -e LLMOPS_DB_URL=postgresql://llmops:llmops@postgres:5432/llmops \
    -v "$PWD/packages/llmops-store:/mig" backend \
    python /mig/migrate_sqlite_to_pg.py --src /app/data/llmops.db
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import aiosqlite  # noqa: E402

from llmops_store import LLMOpsStore, get_db_path  # noqa: E402

# Ephemeral runtime state — pointless (and slightly harmful) to copy; it rebuilds.
SKIP_TABLES = {"leader_lease", "draining", "instances_live", "nodes", "assignments"}


async def _sqlite_tables(src) -> list[str]:
    cur = await src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in await cur.fetchall()]


async def _pg_columns(store, table: str) -> dict[str, str]:
    """{column -> data_type} for a target table (empty if it doesn't exist)."""
    cur = await store._db.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ?",
        (table,),
    )
    return {r["column_name"]: r["data_type"] for r in await cur.fetchall()}


def _coerce(value, pgtype: str):
    """SQLite is loosely typed; coerce to what the Postgres column expects."""
    if value is None:
        return None
    t = pgtype.lower()
    if "double" in t or "real" in t or "numeric" in t:
        return float(value)
    if "int" in t:
        return int(value)
    return value


async def _count(store, table: str) -> int:
    cur = await store._db.execute(f"SELECT COUNT(*) AS c FROM {table}")
    row = await cur.fetchone()
    return int(row["c"]) if row else 0


async def migrate(src_path: str, db_url: str, wipe: bool) -> None:
    if not os.path.exists(src_path):
        raise SystemExit(f"source SQLite not found: {src_path}")
    src = await aiosqlite.connect(src_path)
    src.row_factory = aiosqlite.Row
    store = await LLMOpsStore(db_url=db_url).init()  # creates the schema if needed
    if store._db.dialect != "postgres":
        raise SystemExit("LLMOPS_DB_URL must be a postgres:// DSN (the migration target)")

    try:
        tables = [t for t in await _sqlite_tables(src) if t not in SKIP_TABLES]
        # Keep only tables that exist in the target schema.
        cols_by_table = {t: await _pg_columns(store, t) for t in tables}
        tables = [t for t in tables if cols_by_table[t]]

        existing = {t: await _count(store, t) for t in tables}
        if not wipe and any(existing.values()):
            nonempty = ", ".join(f"{t}={n}" for t, n in existing.items() if n)
            raise SystemExit(
                f"target already has data ({nonempty}); re-run with --wipe to overwrite"
            )
        if wipe:
            for t in tables:
                await store._db.execute(f"TRUNCATE {t} RESTART IDENTITY CASCADE")

        total = 0
        for t in tables:
            target_cols = cols_by_table[t]
            cur = await src.execute(f"SELECT * FROM {t}")
            rows = await cur.fetchall()
            if not rows:
                print(f"  {t:18} 0")
                continue
            cols = [c for c in rows[0].keys() if c in target_cols]
            collist = ", ".join(cols)
            placeholders = ", ".join(["?"] * len(cols))
            for r in rows:
                vals = tuple(_coerce(r[c], target_cols[c]) for c in cols)
                await store._db.execute(
                    f"INSERT INTO {t} ({collist}) VALUES ({placeholders})", vals
                )
            await store._db.commit()
            # Copied explicit ids -> advance the IDENTITY sequence past them.
            if "id" in target_cols:
                await store._db.execute(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {t}), 1))"
                )
            total += len(rows)
            print(f"  {t:18} {len(rows)}")

        print(f"\nMigrated {total} rows across {len(tables)} tables -> Postgres.")
    finally:
        await src.close()
        await store.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Copy the LLMOps store from SQLite to Postgres.")
    ap.add_argument("--src", default=get_db_path(), help="source SQLite file (LLMOPS_DB_PATH)")
    ap.add_argument("--wipe", action="store_true", help="TRUNCATE target tables first (overwrite)")
    args = ap.parse_args()

    db_url = os.environ.get("LLMOPS_DB_URL", "").strip()
    if not db_url:
        raise SystemExit("set LLMOPS_DB_URL to the target Postgres DSN")

    print(f"Migrating {args.src} -> {db_url.rsplit('@', 1)[-1]}"
          + ("  (wipe)" if args.wipe else ""))
    asyncio.run(migrate(args.src, db_url, args.wipe))


if __name__ == "__main__":
    main()

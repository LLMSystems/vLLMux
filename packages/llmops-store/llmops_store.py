"""Shared async persistence for LLMOps telemetry (aiosqlite, WAL).

Both the Dashboard backend and the Router write to the *same* SQLite file:
  - backend records model state-transition events (model_events)
  - router records one row per proxied request (request_logs)

A single shared file (rather than per-app DBs) gives the dashboard one place to
query. WAL mode + a busy timeout let the two processes read/write concurrently.

This is a standalone module (no install): add this directory to sys.path and
`import llmops_store`, mirroring packages/config-schema.

DB location must be an absolute, shared path — the two apps run from different
working directories, so a CWD-relative path would resolve to two different files.
DEFAULT_DB_PATH is therefore derived from this file's location (repo root).
"""
from __future__ import annotations

import os
from typing import Optional

import aiosqlite

# packages/llmops-store/llmops_store.py -> repo root is 2 levels up.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, "data", "llmops.db")
DB_PATH_ENV = "LLMOPS_DB_PATH"


def get_db_path() -> str:
    """Resolve the active DB path: env override, else the shared default."""
    return os.environ.get(DB_PATH_ENV, DEFAULT_DB_PATH)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,
    key        TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    from_state TEXT,
    to_state   TEXT    NOT NULL,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_model_events_key_ts ON model_events(key, ts);

CREATE TABLE IF NOT EXISTS request_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               REAL    NOT NULL,
    model_key        TEXT    NOT NULL,
    instance_id      TEXT,
    path             TEXT    NOT NULL,
    status_code      INTEGER,
    latency_ms       REAL,
    prompt_tokens    INTEGER,
    completion_tokens INTEGER,
    total_tokens     INTEGER,
    error            TEXT,
    api_key_name     TEXT
);
CREATE INDEX IF NOT EXISTS idx_request_logs_model_ts ON request_logs(model_key, ts);

CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    key_hash     TEXT    NOT NULL UNIQUE,
    prefix       TEXT    NOT NULL,
    created_at   REAL    NOT NULL,
    last_used_at REAL,
    revoked      INTEGER NOT NULL DEFAULT 0,
    rpm_limit    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS perf_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL    NOT NULL,
    name        TEXT,
    model       TEXT    NOT NULL,
    target_url  TEXT    NOT NULL,
    status      TEXT    NOT NULL,   -- running | completed | failed | cancelled
    params      TEXT,               -- JSON of the launch config
    result      TEXT,               -- JSON: parsed summary points
    output_dir  TEXT,               -- evalscope raw output directory
    error       TEXT,
    started_at  REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_perf_runs_created ON perf_runs(created_at);
"""

# Columns added after the original schema shipped; applied on init() for DBs
# created before they existed (CREATE TABLE IF NOT EXISTS won't add them).
_MIGRATIONS = [
    ("request_logs", "api_key_name", "TEXT"),
    ("api_keys", "rpm_limit", "INTEGER"),
]


def _percentile(values: list[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile over a list of numbers (pct in 0..100)."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


class LLMOpsStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or get_db_path()
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> "LLMOpsStore":
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.executescript(_SCHEMA)
        await self._migrate()
        await self._db.commit()
        return self

    async def _migrate(self) -> None:
        """Add columns introduced after the original schema, idempotently."""
        for table, column, decl in _MIGRATIONS:
            cur = await self._db.execute(f"PRAGMA table_info({table})")
            cols = {row["name"] for row in await cur.fetchall()}
            if column not in cols:
                await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ---- writes -----------------------------------------------------------

    async def record_model_event(
        self,
        key: str,
        kind: str,
        from_state: Optional[str],
        to_state: str,
        detail: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        import time

        await self._db.execute(
            "INSERT INTO model_events (ts, key, kind, from_state, to_state, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts or time.time(), key, kind, from_state, to_state, detail),
        )
        await self._db.commit()

    async def record_request(
        self,
        model_key: str,
        path: str,
        status_code: Optional[int] = None,
        latency_ms: Optional[float] = None,
        instance_id: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        error: Optional[str] = None,
        api_key_name: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        import time

        await self._db.execute(
            "INSERT INTO request_logs (ts, model_key, instance_id, path, status_code, "
            "latency_ms, prompt_tokens, completion_tokens, total_tokens, error, api_key_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts or time.time(),
                model_key,
                instance_id,
                path,
                status_code,
                latency_ms,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                error,
                api_key_name,
            ),
        )
        await self._db.commit()

    # ---- API keys ---------------------------------------------------------

    async def create_api_key(
        self, name: str, key_hash: str, prefix: str,
        rpm_limit: Optional[int] = None, ts: Optional[float] = None,
    ) -> int:
        import time

        cur = await self._db.execute(
            "INSERT INTO api_keys (name, key_hash, prefix, created_at, rpm_limit) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, key_hash, prefix, ts or time.time(), rpm_limit),
        )
        await self._db.commit()
        return cur.lastrowid

    async def list_api_keys(self) -> list[dict]:
        """All keys, newest first — never returns the hash."""
        cur = await self._db.execute(
            "SELECT id, name, prefix, created_at, last_used_at, revoked, rpm_limit "
            "FROM api_keys ORDER BY id DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_active_api_key_by_hash(self, key_hash: str) -> Optional[dict]:
        """Look up a non-revoked key by its hash (for request authentication)."""
        cur = await self._db.execute(
            "SELECT id, name, prefix, revoked, rpm_limit FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        )
        row = await cur.fetchone()
        if row is None or row["revoked"]:
            return None
        return dict(row)

    # -- Perf runs (load tests) --------------------------------------------

    async def create_perf_run(
        self, model: str, target_url: str, params: str, name: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> int:
        import time

        now = ts or time.time()
        cur = await self._db.execute(
            "INSERT INTO perf_runs (created_at, name, model, target_url, status, params, started_at) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (now, name, model, target_url, params, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def finish_perf_run(
        self, run_id: int, status: str, result: Optional[str] = None,
        output_dir: Optional[str] = None, error: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        import time

        await self._db.execute(
            "UPDATE perf_runs SET status = ?, result = ?, output_dir = ?, error = ?, "
            "finished_at = ? WHERE id = ?",
            (status, result, output_dir, error, ts or time.time(), run_id),
        )
        await self._db.commit()

    async def list_perf_runs(self, limit: int = 50) -> list[dict]:
        cur = await self._db.execute(
            "SELECT id, created_at, name, model, target_url, status, params, "
            "output_dir, error, started_at, finished_at "
            "FROM perf_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_perf_run(self, run_id: int) -> Optional[dict]:
        cur = await self._db.execute("SELECT * FROM perf_runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_perf_run(self, run_id: int) -> bool:
        cur = await self._db.execute("DELETE FROM perf_runs WHERE id = ?", (run_id,))
        await self._db.commit()
        return cur.rowcount > 0

    async def mark_stale_perf_runs(self) -> None:
        """On startup, any 'running' row is orphaned (its process died with the
        previous backend) — flip it to failed so it doesn't linger forever."""
        await self._db.execute(
            "UPDATE perf_runs SET status = 'failed', error = 'interrupted by restart' "
            "WHERE status = 'running'"
        )
        await self._db.commit()

    async def api_key_usage(self) -> dict[str, dict]:
        """Per-key request aggregates keyed by key name (from request_logs)."""
        cur = await self._db.execute(
            "SELECT api_key_name AS name, COUNT(*) AS request_count, "
            "COALESCE(SUM(total_tokens), 0) AS total_tokens, MAX(ts) AS last_ts "
            "FROM request_logs WHERE api_key_name IS NOT NULL GROUP BY api_key_name"
        )
        return {r["name"]: dict(r) for r in await cur.fetchall()}

    async def revoke_api_key(self, key_id: int) -> bool:
        cur = await self._db.execute(
            "UPDATE api_keys SET revoked = 1 WHERE id = ? AND revoked = 0", (key_id,)
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def touch_api_key(self, key_id: int, ts: Optional[float] = None) -> None:
        """Best-effort last-used timestamp (skipped silently on contention)."""
        import time

        try:
            await self._db.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?", (ts or time.time(), key_id)
            )
            await self._db.commit()
        except Exception:
            pass

    # ---- reads ------------------------------------------------------------

    async def recent_events(self, key: Optional[str] = None, limit: int = 100) -> list[dict]:
        if key is None:
            cur = await self._db.execute(
                "SELECT * FROM model_events ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM model_events WHERE key = ? ORDER BY id DESC LIMIT ?",
                (key, limit),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def recent_requests(
        self, model_key: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        if model_key is None:
            cur = await self._db.execute(
                "SELECT * FROM request_logs ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM request_logs WHERE model_key = ? ORDER BY id DESC LIMIT ?",
                (model_key, limit),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def usage_summary(self, since: Optional[float] = None) -> list[dict]:
        """Per-model request aggregates. p50/p95 computed in Python (SQLite has
        no percentile aggregate)."""
        where = "WHERE ts >= ?" if since is not None else ""
        params: tuple = (since,) if since is not None else ()
        cur = await self._db.execute(
            f"""
            SELECT model_key,
                   COUNT(*)                                   AS count,
                   SUM(CASE WHEN status_code >= 400 OR error IS NOT NULL THEN 1 ELSE 0 END) AS error_count,
                   AVG(latency_ms)                            AS avg_latency_ms,
                   MAX(latency_ms)                            AS max_latency_ms,
                   COALESCE(SUM(total_tokens), 0)             AS total_tokens
            FROM request_logs {where}
            GROUP BY model_key
            ORDER BY count DESC
            """,
            params,
        )
        rows = [dict(r) for r in await cur.fetchall()]

        for row in rows:
            lat_cur = await self._db.execute(
                f"SELECT latency_ms FROM request_logs "
                f"WHERE model_key = ? {('AND ts >= ?' if since is not None else '')} "
                f"AND latency_ms IS NOT NULL",
                (row["model_key"], since) if since is not None else (row["model_key"],),
            )
            latencies = [r[0] for r in await lat_cur.fetchall()]
            row["p50_latency_ms"] = _percentile(latencies, 50)
            row["p95_latency_ms"] = _percentile(latencies, 95)
        return rows

    async def timeseries(
        self,
        since: float,
        bucket_seconds: int = 60,
        model_key: Optional[str] = None,
    ) -> list[dict]:
        """Request metrics bucketed into fixed time windows for trend charts.

        Each bucket carries request count, error count, avg + p95 latency, and
        total tokens. Buckets align to `bucket_seconds`; `ts` is the bucket start.
        """
        bucket = max(1, int(bucket_seconds))
        where = "WHERE ts >= ?"
        params: list = [since]
        if model_key:
            where += " AND model_key = ?"
            params.append(model_key)

        cur = await self._db.execute(
            f"""
            SELECT CAST(ts / ? AS INTEGER) * ? AS bucket,
                   COUNT(*)                                   AS count,
                   SUM(CASE WHEN status_code >= 400 OR error IS NOT NULL THEN 1 ELSE 0 END) AS error_count,
                   AVG(latency_ms)                            AS avg_latency_ms,
                   COALESCE(SUM(total_tokens), 0)             AS total_tokens
            FROM request_logs {where}
            GROUP BY bucket
            ORDER BY bucket
            """,
            (bucket, bucket, *params),
        )
        rows = [dict(r) for r in await cur.fetchall()]

        # p95 per bucket (SQLite has no percentile aggregate).
        lat_cur = await self._db.execute(
            f"""
            SELECT CAST(ts / ? AS INTEGER) * ? AS bucket, latency_ms
            FROM request_logs {where} AND latency_ms IS NOT NULL
            """,
            (bucket, bucket, *params),
        )
        by_bucket: dict[int, list[float]] = {}
        for r in await lat_cur.fetchall():
            by_bucket.setdefault(int(r[0]), []).append(r[1])

        for row in rows:
            b = int(row.pop("bucket"))
            row["ts"] = b
            row["p95_latency_ms"] = _percentile(by_bucket.get(b, []), 95)
        return rows

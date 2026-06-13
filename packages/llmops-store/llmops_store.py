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
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_request_logs_model_ts ON request_logs(model_key, ts);
"""


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
        await self._db.commit()
        return self

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
        ts: Optional[float] = None,
    ) -> None:
        import time

        await self._db.execute(
            "INSERT INTO request_logs (ts, model_key, instance_id, path, status_code, "
            "latency_ms, prompt_tokens, completion_tokens, total_tokens, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            ),
        )
        await self._db.commit()

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

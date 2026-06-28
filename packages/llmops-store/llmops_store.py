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
    rpm_limit    INTEGER,
    token_quota  INTEGER,            -- max total_tokens per quota_period; NULL = unlimited
    quota_period TEXT                -- 'total' | 'daily' | 'monthly' (NULL == 'total')
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

CREATE TABLE IF NOT EXISTS eval_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL    NOT NULL,
    name        TEXT,
    model       TEXT    NOT NULL,
    target_url  TEXT    NOT NULL,
    datasets    TEXT    NOT NULL,      -- JSON list of dataset ids
    status      TEXT    NOT NULL,      -- queued | running | completed | failed | cancelled
    params      TEXT,                  -- JSON of the launch config
    result      TEXT,                  -- JSON: {dataset: {metric: score, ...}, ...}
    output_dir  TEXT,                  -- evalscope raw output directory
    error       TEXT,
    started_at  REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_created ON eval_runs(created_at);

CREATE TABLE IF NOT EXISTS operators (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT    NOT NULL,            -- display name + DiceBear avatar seed
    token_hash   TEXT    NOT NULL UNIQUE,     -- SHA-256 only; plaintext shown once
    prefix       TEXT    NOT NULL,            -- display prefix sk-op-xxxx…last4
    role         TEXT    NOT NULL,            -- viewer | operator | admin
    created_at   REAL    NOT NULL,
    last_used_at REAL,
    revoked      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_operators_hash ON operators(token_hash);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    actor       TEXT    NOT NULL,      -- operator label / 'admin' / 'local-dev'
    role        TEXT,                  -- viewer | operator | admin
    method      TEXT    NOT NULL,      -- POST/PUT/DELETE/PATCH
    path        TEXT    NOT NULL,      -- /api/models/Qwen3-0.6B/autoscale
    target      TEXT,                  -- subject extracted from the path
    status      INTEGER NOT NULL,      -- response HTTP status code
    detail      TEXT,                  -- redacted request summary (JSON)
    source_ip   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor);
"""

# Columns added after the original schema shipped; applied on init() for DBs
# created before they existed (CREATE TABLE IF NOT EXISTS won't add them).
_MIGRATIONS = [
    ("request_logs", "api_key_name", "TEXT"),
    ("api_keys", "rpm_limit", "INTEGER"),
    ("api_keys", "token_quota", "INTEGER"),
    ("api_keys", "quota_period", "TEXT"),
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
        # isolation_level=None -> autocommit: every statement is its own committed
        # unit, so a write that loses the WAL lock can never leave a dangling
        # implicit transaction open (which would wedge the write lock for every
        # process until the connection writes again — see the eval finalize hang).
        # The explicit commit() calls below become harmless no-ops.
        self._db = await aiosqlite.connect(self.db_path, isolation_level=None)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        # Wait (rather than erroring) up to 30s for a contended write lock; under a
        # parallel eval the router + backend write concurrently.
        await self._db.execute("PRAGMA busy_timeout=30000")
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
        rpm_limit: Optional[int] = None, token_quota: Optional[int] = None,
        quota_period: Optional[str] = None, ts: Optional[float] = None,
    ) -> int:
        import time

        cur = await self._db.execute(
            "INSERT INTO api_keys "
            "(name, key_hash, prefix, created_at, rpm_limit, token_quota, quota_period) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, key_hash, prefix, ts or time.time(), rpm_limit, token_quota, quota_period),
        )
        await self._db.commit()
        return cur.lastrowid

    async def list_api_keys(self) -> list[dict]:
        """All keys, newest first — never returns the hash."""
        cur = await self._db.execute(
            "SELECT id, name, prefix, created_at, last_used_at, revoked, rpm_limit, "
            "token_quota, quota_period FROM api_keys ORDER BY id DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_active_api_key_by_hash(self, key_hash: str) -> Optional[dict]:
        """Look up a non-revoked key by its hash (for request authentication)."""
        cur = await self._db.execute(
            "SELECT id, name, prefix, revoked, rpm_limit, token_quota, quota_period "
            "FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        )
        row = await cur.fetchone()
        if row is None or row["revoked"]:
            return None
        return dict(row)

    async def tokens_used_by_key(self, name: str, since: Optional[float] = None) -> int:
        """Sum of total_tokens attributed to one key name, optionally since a
        timestamp (the start of the current quota window). Used for quota checks."""
        where = "api_key_name = ?"
        params: tuple = (name,)
        if since is not None:
            where += " AND ts >= ?"
            params = (name, since)
        cur = await self._db.execute(
            f"SELECT COALESCE(SUM(total_tokens), 0) AS used FROM request_logs WHERE {where}",
            params,
        )
        row = await cur.fetchone()
        return int(row["used"] or 0)

    # ---- Operators (control-plane users) ---------------------------------

    async def create_operator(
        self, label: str, token_hash: str, prefix: str, role: str,
        ts: Optional[float] = None,
    ) -> int:
        import time

        cur = await self._db.execute(
            "INSERT INTO operators (label, token_hash, prefix, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (label, token_hash, prefix, role, ts or time.time()),
        )
        await self._db.commit()
        return cur.lastrowid

    async def list_operators(self) -> list[dict]:
        """All operators, newest first — never returns the hash."""
        cur = await self._db.execute(
            "SELECT id, label, prefix, role, created_at, last_used_at, revoked "
            "FROM operators ORDER BY id DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def count_active_operators(self) -> int:
        """Number of non-revoked operators (0 => fall back to env admin token)."""
        cur = await self._db.execute(
            "SELECT COUNT(*) AS n FROM operators WHERE revoked = 0"
        )
        row = await cur.fetchone()
        return int(row["n"] or 0)

    async def get_active_operator_by_hash(self, token_hash: str) -> Optional[dict]:
        """Look up a non-revoked operator by token hash (for authentication)."""
        cur = await self._db.execute(
            "SELECT id, label, prefix, role, revoked FROM operators WHERE token_hash = ?",
            (token_hash,),
        )
        row = await cur.fetchone()
        if row is None or row["revoked"]:
            return None
        return dict(row)

    async def touch_operator(self, operator_id: int, ts: Optional[float] = None) -> None:
        import time

        await self._db.execute(
            "UPDATE operators SET last_used_at = ? WHERE id = ?",
            (ts or time.time(), operator_id),
        )
        await self._db.commit()

    async def revoke_operator(self, operator_id: int) -> bool:
        cur = await self._db.execute(
            "UPDATE operators SET revoked = 1 WHERE id = ? AND revoked = 0", (operator_id,)
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def set_operator_role(self, operator_id: int, role: str) -> bool:
        cur = await self._db.execute(
            "UPDATE operators SET role = ? WHERE id = ? AND revoked = 0",
            (role, operator_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def rotate_operator_token(
        self, operator_id: int, token_hash: str, prefix: str
    ) -> bool:
        """Replace an operator's token (revokes the old one immediately)."""
        cur = await self._db.execute(
            "UPDATE operators SET token_hash = ?, prefix = ?, last_used_at = NULL "
            "WHERE id = ? AND revoked = 0",
            (token_hash, prefix, operator_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ---- Audit log (control-plane mutations) -----------------------------

    async def record_audit(
        self, actor: str, method: str, path: str, status: int,
        role: Optional[str] = None, target: Optional[str] = None,
        detail: Optional[str] = None, source_ip: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        import time

        await self._db.execute(
            "INSERT INTO audit_log (ts, actor, role, method, path, target, status, detail, source_ip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts or time.time(), actor, role, method, path, target, status, detail, source_ip),
        )
        await self._db.commit()

    async def list_audit(
        self, actor: Optional[str] = None, action: Optional[str] = None,
        target: Optional[str] = None, since: Optional[float] = None,
        until: Optional[float] = None, before: Optional[int] = None, limit: int = 200,
    ) -> list[dict]:
        """Audit entries, newest first, with optional filters. ``action`` matches
        the path substring (e.g. 'autoscale'); ``actor``/``target`` are exact;
        ``before`` is an id cursor (rows with a smaller id) for pagination."""
        where: list[str] = []
        params: list = []
        if actor is not None:
            where.append("actor = ?"); params.append(actor)
        if action is not None:
            where.append("path LIKE ?"); params.append(f"%{action}%")
        if target is not None:
            where.append("target = ?"); params.append(target)
        if since is not None:
            where.append("ts >= ?"); params.append(since)
        if until is not None:
            where.append("ts <= ?"); params.append(until)
        if before is not None:
            where.append("id < ?"); params.append(before)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        params.append(max(1, min(limit, 1000)))
        cur = await self._db.execute(
            f"SELECT id, ts, actor, role, method, path, target, status, detail, source_ip "
            f"FROM audit_log{clause} ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def prune_audit(self, max_rows: int = 50_000) -> int:
        """Cap the audit table to its most recent ``max_rows`` rows. Returns the
        number deleted."""
        cur = await self._db.execute(
            "DELETE FROM audit_log WHERE id NOT IN "
            "(SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)",
            (max_rows,),
        )
        await self._db.commit()
        return cur.rowcount

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

    # -- Eval runs (accuracy / quality benchmarks) -------------------------

    async def create_eval_run(
        self, model: str, target_url: str, datasets: str, params: str,
        name: Optional[str] = None, ts: Optional[float] = None,
        status: str = "running",
    ) -> int:
        import time

        now = ts or time.time()
        # A 'queued' run hasn't actually started yet, so leave started_at unset
        # until start_eval_run flips it to 'running'.
        started_at = None if status == "queued" else now
        cur = await self._db.execute(
            "INSERT INTO eval_runs (created_at, name, model, target_url, datasets, "
            "status, params, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, name, model, target_url, datasets, status, params, started_at),
        )
        await self._db.commit()
        return cur.lastrowid

    async def start_eval_run(self, run_id: int, ts: Optional[float] = None) -> None:
        """Flip a queued run to 'running' once a concurrency slot frees up."""
        import time

        await self._db.execute(
            "UPDATE eval_runs SET status = 'running', started_at = ? "
            "WHERE id = ? AND status = 'queued'",
            (ts or time.time(), run_id),
        )
        await self._db.commit()

    async def finish_eval_run(
        self, run_id: int, status: str, result: Optional[str] = None,
        output_dir: Optional[str] = None, error: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        import time

        await self._db.execute(
            "UPDATE eval_runs SET status = ?, result = ?, output_dir = ?, error = ?, "
            "finished_at = ? WHERE id = ?",
            (status, result, output_dir, error, ts or time.time(), run_id),
        )
        await self._db.commit()

    async def list_eval_runs(self, limit: int = 50) -> list[dict]:
        cur = await self._db.execute(
            "SELECT id, created_at, name, model, target_url, datasets, status, params, "
            "result, output_dir, error, started_at, finished_at "
            "FROM eval_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_eval_run(self, run_id: int) -> Optional[dict]:
        cur = await self._db.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_eval_run(self, run_id: int) -> bool:
        cur = await self._db.execute("DELETE FROM eval_runs WHERE id = ?", (run_id,))
        await self._db.commit()
        return cur.rowcount > 0

    async def mark_stale_eval_runs(self) -> None:
        """On startup, flip orphaned 'running' rows (process died with the previous
        backend) to failed so they don't linger forever."""
        await self._db.execute(
            "UPDATE eval_runs SET status = 'failed', error = 'interrupted by restart' "
            "WHERE status IN ('running', 'queued')"
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

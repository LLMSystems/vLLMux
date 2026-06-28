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

from _driver import make_driver

# packages/llmops-store/llmops_store.py -> repo root is 2 levels up.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, "data", "llmops.db")
DB_PATH_ENV = "LLMOPS_DB_PATH"
DB_URL_ENV = "LLMOPS_DB_URL"  # postgres://… enables the HA (Postgres) backend


def get_db_path() -> str:
    """Resolve the active DB path: env override, else the shared default."""
    return os.environ.get(DB_PATH_ENV, DEFAULT_DB_PATH)


def get_db_url() -> Optional[str]:
    """Postgres DSN if HA mode is on (LLMOPS_DB_URL set), else None (SQLite)."""
    return os.environ.get(DB_URL_ENV, "").strip() or None


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

CREATE TABLE IF NOT EXISTS alert_sinks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT    NOT NULL,            -- slack | discord | webhook
    url          TEXT    NOT NULL,
    min_severity TEXT    NOT NULL DEFAULT 'info',
    created_at   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS model_prices (
    model         TEXT    PRIMARY KEY,       -- route/group name (request_logs.model_key)
    input_price   REAL    NOT NULL,          -- price per 1M prompt tokens
    output_price  REAL    NOT NULL,          -- price per 1M completion tokens
    currency      TEXT    NOT NULL DEFAULT 'USD',
    updated_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS config_versions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,
    actor     TEXT,                  -- operator label / 'admin' / 'local-dev'
    role      TEXT,                  -- viewer | operator | admin
    summary   TEXT,                  -- trigger, e.g. 'PUT /api/models/Qwen/autoscale'
    sha256    TEXT    NOT NULL,      -- overlay content hash (skip if == latest)
    overlay   TEXT    NOT NULL       -- full overlay JSON snapshot
);
CREATE INDEX IF NOT EXISTS idx_config_versions_ts ON config_versions(ts);
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
    def __init__(self, db_path: Optional[str] = None, db_url: Optional[str] = None) -> None:
        # db_url (LLMOPS_DB_URL=postgres://…) selects the HA Postgres backend;
        # otherwise the shared SQLite file. db_path is ignored in Postgres mode.
        self.db_url = db_url or get_db_url()
        self.db_path = db_path or get_db_path()
        self._db = None  # driver (SQLite or Postgres), set in init()

    async def init(self) -> "LLMOpsStore":
        # The driver owns connection setup (WAL/pragmas for SQLite, a pool for
        # Postgres). Every statement self-commits (autocommit), so the explicit
        # commit() calls below are harmless no-ops on both backends.
        self._db = await make_driver(self.db_path, self.db_url).connect()
        await self._db.executescript(_SCHEMA)
        await self._migrate()
        return self

    async def _migrate(self) -> None:
        """Add columns introduced after the original schema, idempotently."""
        for table, column, decl in _MIGRATIONS:
            await self._db.ensure_column(table, column, decl)

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

        return await self._db.insert(
            "INSERT INTO api_keys "
            "(name, key_hash, prefix, created_at, rpm_limit, token_quota, quota_period) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, key_hash, prefix, ts or time.time(), rpm_limit, token_quota, quota_period),
        )

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

        return await self._db.insert(
            "INSERT INTO operators (label, token_hash, prefix, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (label, token_hash, prefix, role, ts or time.time()),
        )

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

    # ---- Alert sinks (notification destinations) -------------------------

    async def create_alert_sink(
        self, type: str, url: str, min_severity: str = "info", ts: Optional[float] = None
    ) -> int:
        import time

        return await self._db.insert(
            "INSERT INTO alert_sinks (type, url, min_severity, created_at) VALUES (?, ?, ?, ?)",
            (type, url, min_severity, ts or time.time()),
        )

    async def list_alert_sinks(self) -> list[dict]:
        cur = await self._db.execute(
            "SELECT id, type, url, min_severity, created_at FROM alert_sinks ORDER BY id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_alert_sink(self, sink_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT id, type, url, min_severity, created_at FROM alert_sinks WHERE id = ?",
            (sink_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_alert_sink(self, sink_id: int) -> bool:
        cur = await self._db.execute("DELETE FROM alert_sinks WHERE id = ?", (sink_id,))
        await self._db.commit()
        return cur.rowcount > 0

    # ---- Model prices + cost aggregates ----------------------------------

    async def set_model_price(
        self, model: str, input_price: float, output_price: float,
        currency: str = "USD", ts: Optional[float] = None,
    ) -> None:
        """Upsert the per-1M-token price for a model (route/group name)."""
        import time

        await self._db.execute(
            "INSERT INTO model_prices (model, input_price, output_price, currency, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(model) DO UPDATE SET input_price=excluded.input_price, "
            "output_price=excluded.output_price, currency=excluded.currency, "
            "updated_at=excluded.updated_at",
            (model, input_price, output_price, currency, ts or time.time()),
        )
        await self._db.commit()

    async def list_model_prices(self) -> list[dict]:
        cur = await self._db.execute(
            "SELECT model, input_price, output_price, currency, updated_at "
            "FROM model_prices ORDER BY model"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def delete_model_price(self, model: str) -> bool:
        cur = await self._db.execute("DELETE FROM model_prices WHERE model = ?", (model,))
        await self._db.commit()
        return cur.rowcount > 0

    async def token_usage_by_model(
        self, since: Optional[float] = None, until: Optional[float] = None
    ) -> list[dict]:
        """Per-model prompt/completion/total token sums + request count, for costing."""
        where, params = self._ts_window(since, until)
        cur = await self._db.execute(
            f"""
            SELECT model_key,
                   COUNT(*)                              AS requests,
                   COALESCE(SUM(prompt_tokens), 0)       AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0)   AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0)        AS total_tokens
            FROM request_logs {where}
            GROUP BY model_key ORDER BY total_tokens DESC
            """,
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def token_usage_by_key(
        self, since: Optional[float] = None, until: Optional[float] = None
    ) -> list[dict]:
        """Per-API-key prompt/completion/total token sums + request count.

        Cost needs each key's split across models (different prices), so this
        returns one row per (key, model)."""
        base, params = self._ts_window(since, until)
        where = (base + " AND " if base else "WHERE ") + "api_key_name IS NOT NULL"
        cur = await self._db.execute(
            f"""
            SELECT api_key_name AS name, model_key,
                   COUNT(*)                              AS requests,
                   COALESCE(SUM(prompt_tokens), 0)       AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0)   AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0)        AS total_tokens
            FROM request_logs {where}
            GROUP BY api_key_name, model_key
            """,
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    @staticmethod
    def _ts_window(since: Optional[float], until: Optional[float]) -> tuple[str, tuple]:
        clauses, params = [], []
        if since is not None:
            clauses.append("ts >= ?"); params.append(since)
        if until is not None:
            clauses.append("ts <= ?"); params.append(until)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, tuple(params)

    # ---- Config versions (overlay snapshots) -----------------------------

    async def latest_config_version_hash(self) -> Optional[str]:
        cur = await self._db.execute(
            "SELECT sha256 FROM config_versions ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return row["sha256"] if row else None

    async def record_config_version(
        self, overlay: str, sha256: str, actor: Optional[str] = None,
        role: Optional[str] = None, summary: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> Optional[int]:
        """Append an overlay snapshot. Skips (returns None) when the content hash
        matches the latest row, so identical-content requests don't pile up."""
        import time

        if await self.latest_config_version_hash() == sha256:
            return None
        return await self._db.insert(
            "INSERT INTO config_versions (ts, actor, role, summary, sha256, overlay) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts or time.time(), actor, role, summary, sha256, overlay),
        )

    async def list_config_versions(
        self, before: Optional[int] = None, limit: int = 50
    ) -> list[dict]:
        """Version metadata (no overlay body), newest first; ``before`` is an id
        cursor for pagination."""
        where, params = "", []
        if before is not None:
            where = " WHERE id < ?"; params.append(before)
        params.append(max(1, min(limit, 500)))
        cur = await self._db.execute(
            f"SELECT id, ts, actor, role, summary, sha256 FROM config_versions{where} "
            f"ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_config_version(self, version_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT id, ts, actor, role, summary, sha256, overlay FROM config_versions "
            "WHERE id = ?",
            (version_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def prune_config_versions(self, max_rows: int = 500) -> int:
        cur = await self._db.execute(
            "DELETE FROM config_versions WHERE id NOT IN "
            "(SELECT id FROM config_versions ORDER BY id DESC LIMIT ?)",
            (max_rows,),
        )
        await self._db.commit()
        return cur.rowcount

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
        return await self._db.insert(
            "INSERT INTO perf_runs (created_at, name, model, target_url, status, params, started_at) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (now, name, model, target_url, params, now),
        )

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
        return await self._db.insert(
            "INSERT INTO eval_runs (created_at, name, model, target_url, datasets, "
            "status, params, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, name, model, target_url, datasets, status, params, started_at),
        )

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

"""API-key authentication for inference endpoints.

Enforcement is opt-in via ``LLMOPS_REQUIRE_API_KEY`` so local/dev stays open.
When on, a request must carry ``Authorization: Bearer <token>`` where the token
is either the shared admin token (``LLMOPS_ADMIN_TOKEN``, used by the dashboard
itself) or a non-revoked API key minted by the backend. The resolved key name
is returned so the request can be attributed in the telemetry store.

A tiny in-process TTL cache keeps the hot inference path off SQLite for repeat
keys; revocation therefore takes effect within ``_CACHE_TTL`` seconds.
"""
from __future__ import annotations

import datetime
import hashlib
import os
import time
from collections import deque

from fastapi import HTTPException, Request, status

_CACHE_TTL = 30.0
# hash -> (key_id_or_None, name, rpm_limit, token_quota, quota_period, expires_at)
_cache: dict[str, tuple[int | None, str, int | None, int | None, str | None, float]] = {}
# key name -> request timestamps in the trailing 60s (sliding-window limiter)
_hits: dict[str, deque] = {}
# Token-quota usage cache: name -> (used_tokens, period_start, expires_at).
# Quotas are enforced *softly* — token counts are only known after a response
# completes, so we admit on the running total and reject once it's over. A short
# TTL keeps the hot path off SQLite without letting overruns drift far.
_QUOTA_TTL = 15.0
_quota_cache: dict[str, tuple[int, float, float]] = {}


def _check_rate(name: str, rpm_limit: int | None) -> None:
    if not rpm_limit:
        return
    now = time.monotonic()
    dq = _hits.setdefault(name, deque())
    while dq and dq[0] <= now - 60.0:
        dq.popleft()
    if len(dq) >= rpm_limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"rate limit exceeded for key '{name}' ({rpm_limit}/min)",
            headers={"Retry-After": "60"},
        )
    dq.append(now)


def _period_start(quota_period: str | None) -> float:
    """Epoch-seconds start of the current quota window (UTC), matching the
    time.time() clock written to request_logs.ts. 'total' (or None) == all time."""
    if quota_period == "daily":
        now = datetime.datetime.now(datetime.timezone.utc)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if quota_period == "monthly":
        now = datetime.datetime.now(datetime.timezone.utc)
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return 0.0


async def _check_quota(
    store, name: str, token_quota: int | None, quota_period: str | None
) -> None:
    if not token_quota or store is None:
        return
    period_start = _period_start(quota_period)
    now = time.monotonic()
    cached = _quota_cache.get(name)
    if cached and cached[1] == period_start and cached[2] > now:
        used = cached[0]
    else:
        used = await store.tokens_used_by_key(name, period_start or None)
        _quota_cache[name] = (used, period_start, now + _QUOTA_TTL)
    if used >= token_quota:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"token quota exceeded for key '{name}' "
            f"({used}/{token_quota} tokens, {quota_period or 'total'})",
            headers={"Retry-After": "60"},
        )


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _require_enabled() -> bool:
    return os.environ.get("LLMOPS_REQUIRE_API_KEY", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


async def authenticate(request: Request) -> str | None:
    """Return the attributed key name, or None when auth is disabled.

    Raises 401 when enforcement is on and the bearer token is missing/invalid.
    """
    if not _require_enabled():
        return None

    token = _bearer(request)
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # The dashboard's own admin token is always accepted (attributed "dashboard",
    # never rate-limited).
    admin = os.environ.get("LLMOPS_ADMIN_TOKEN", "").strip()
    if admin and token == admin:
        return "dashboard"

    key_hash = _hash_key(token)
    now = time.monotonic()
    store = getattr(request.app.state, "store", None)
    cached = _cache.get(key_hash)
    if cached and cached[5] > now:
        _check_rate(cached[1], cached[2])
        await _check_quota(store, cached[1], cached[3], cached[4])
        return cached[1]

    row = await store.get_active_api_key_by_hash(key_hash) if store is not None else None
    if row is None:
        # Fall back to a control-plane operator token: a signed-in dashboard user
        # (viewer/operator/admin) may drive the playground. Attributed by label,
        # never rate-limited/quota'd — like the admin token. Only reached when the
        # token isn't a real API key, so API-key traffic pays no extra lookup.
        op = (
            await store.get_active_operator_by_hash(key_hash)
            if store is not None and hasattr(store, "get_active_operator_by_hash")
            else None
        )
        if op is not None:
            # Viewers are read-only on the control plane and may not run inference.
            if op.get("role") == "viewer":
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "viewer role cannot run inference",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            name = op["label"]
            _cache[key_hash] = (None, name, None, None, None, now + _CACHE_TTL)
            return name
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid or revoked API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _cache[key_hash] = (
        row["id"], row["name"], row.get("rpm_limit"),
        row.get("token_quota"), row.get("quota_period"), now + _CACHE_TTL,
    )
    _check_rate(row["name"], row.get("rpm_limit"))
    await _check_quota(store, row["name"], row.get("token_quota"), row.get("quota_period"))
    return row["name"]

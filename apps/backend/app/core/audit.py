"""Audit middleware: records every control-plane mutation to the store.

A single ASGI middleware captures all state-changing requests (POST/PUT/PATCH/
DELETE under /api) with their resolved actor, target, status and a *redacted*
body summary — so we get ~full coverage without touching each route. It only
records facts; a failure here never affects the response.

See docs/rbac-audit-design_zh-CN.md §Phase 1.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("llmops.audit")

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# Noisy / non-mutating-in-spirit endpoints we don't want in the audit trail.
_SKIP_PATHS = {"/api/auth/verify"}
# Substrings whose values must never be persisted (redacted recursively).
_SECRET_HINTS = ("token", "password", "secret", "authorization", "api_key", "apikey", "plaintext")
_MAX_BODY = 4096          # only inspect bodies up to this size
_MAX_DETAIL = 2000        # cap the stored JSON summary


def extract_target(path: str) -> str | None:
    """The subject of the action — the id segment after the resource noun.
    e.g. /api/models/Qwen3-0.6B/autoscale → 'Qwen3-0.6B', /api/keys/3 → '3'."""
    segments = path.split("/")  # ['', 'api', 'models', '<id>', ...]
    if len(segments) > 3 and segments[1] == "api" and segments[3]:
        return segments[3]
    return None


def _redact(value):
    """Recursively redact secret-looking fields; keep structure for context."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(h in k.lower() for h in _SECRET_HINTS):
                out[k] = "***redacted***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def redact_body(raw: bytes, content_type: str | None) -> str | None:
    """Parse a small JSON body and return a redacted, size-capped JSON summary."""
    if not raw or content_type is None or "application/json" not in content_type:
        return None
    if len(raw) > _MAX_BODY:
        return json.dumps({"_note": "body too large to audit", "bytes": len(raw)})
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    summary = json.dumps(_redact(parsed), ensure_ascii=False, separators=(",", ":"))
    return summary[:_MAX_DETAIL]


def install_audit_middleware(app) -> None:
    @app.middleware("http")
    async def audit_middleware(request, call_next):
        path = request.url.path
        method = request.method.upper()
        record = (
            method in _MUTATING
            and path.startswith("/api/")
            and path not in _SKIP_PATHS
        )

        # Capture the body before the route consumes it (Starlette caches it, so
        # the downstream handler can still read it). Only for small JSON bodies.
        detail = None
        if record:
            try:
                raw = await request.body()
                detail = redact_body(raw, request.headers.get("content-type"))
            except Exception:  # never let auditing break the request
                detail = None

        response = await call_next(request)

        if record:
            try:
                store = getattr(request.app.state, "store", None)
                if store is not None:
                    actor = getattr(request.state, "actor", None) or "anonymous"
                    role = getattr(request.state, "role", None)
                    await store.record_audit(
                        actor=actor, role=role, method=method, path=path,
                        target=extract_target(path), status=response.status_code,
                        detail=detail,
                        source_ip=request.client.host if request.client else None,
                    )
            except Exception:  # pragma: no cover - audit must never 500 a request
                logger.warning("failed to record audit entry for %s %s", method, path, exc_info=True)

        return response

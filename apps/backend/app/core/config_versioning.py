"""Config-version middleware: snapshot the overlay whenever a request changes it.

Every runtime config change lands in the overlay file (data/dynamic_models.json)
via the single save_overlay() chokepoint. Rather than hook that sync, store-less
function in a dozen manager call sites, we diff the overlay's content hash around
each mutating /api request: if it changed and the request succeeded, we persist a
full snapshot tagged with the resolved actor — total coverage, zero manager
changes, and each version maps 1:1 to an audit entry.

See docs/config-versioning-design_zh-CN.md §Phase 2.
"""
from __future__ import annotations

import hashlib
import json
import logging

from app.services.overlay import load_overlay

logger = logging.getLogger("llmops.config_versions")

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def overlay_blob_and_hash() -> tuple[str, str]:
    """Canonical (sorted) JSON of the current overlay + its sha256, so identical
    content always yields an identical hash regardless of on-disk formatting."""
    blob = json.dumps(load_overlay(), sort_keys=True, ensure_ascii=False)
    return blob, hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def snapshot_overlay(store, *, actor=None, role=None, summary=None) -> int | None:
    """Persist a version row if the overlay differs from the latest snapshot."""
    blob, digest = overlay_blob_and_hash()
    return await store.record_config_version(
        overlay=blob, sha256=digest, actor=actor, role=role, summary=summary
    )


def install_config_version_middleware(app) -> None:
    @app.middleware("http")
    async def config_version_middleware(request, call_next):
        path = request.url.path
        watch = request.method.upper() in _MUTATING and path.startswith("/api/")
        before = overlay_blob_and_hash()[1] if watch else None

        response = await call_next(request)

        if watch and response.status_code < 400:
            try:
                _, after = overlay_blob_and_hash()
                store = getattr(request.app.state, "store", None)
                if after != before and store is not None:
                    actor = getattr(request.state, "actor", None) or "anonymous"
                    role = getattr(request.state, "role", None)
                    await snapshot_overlay(
                        store, actor=actor, role=role,
                        summary=f"{request.method.upper()} {path}",
                    )
            except Exception:  # pragma: no cover - versioning must never 500 a request
                logger.warning("failed to snapshot overlay for %s %s",
                               request.method, path, exc_info=True)

        return response

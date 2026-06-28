"""Config inspection + versioning (export / import / history / rollback).

The hand-maintained config.yaml is read-only to the app; every runtime change
lives in the dynamic-model overlay (data/dynamic_models.json). That overlay is
therefore the unit we back up, version and roll back — see
docs/config-versioning-design_zh-CN.md.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import get_manager
from app.core.auth import require_admin, require_operator
from app.core.config_versioning import overlay_blob_and_hash
from app.llmops.manager import ConfigInvalid, ModelConflict, ModelManager
from app.services.config_service import summarize_config
from app.services.overlay import build_merged_config

router = APIRouter(tags=["config"])

EXPORT_VERSION = 1


@router.get("/config")
def get_config():
    # Merge base config.yaml with the dynamic-model overlay so the summary
    # reflects models added at runtime, not just what's on disk.
    return summarize_config(build_merged_config())


# -- Export / import (backup & restore) --------------------------------------

@router.get("/config/export", dependencies=[Depends(require_operator)])
def export_config(manager: ModelManager = Depends(get_manager)):
    """Download the full overlay (the entire mutable state) as a portable file.

    Wrapped in metadata so an importer can sanity-check it; the overlay holds no
    secrets (tokens/keys live in the DB/env), so it's safe to carry off-box."""
    return {
        "version": EXPORT_VERSION,
        "exported_at": time.time(),
        "overlay": manager.export_overlay(),
    }


class ImportRequest(BaseModel):
    # Accept either a full export wrapper ({version, overlay}) or a bare overlay
    # ({"LLM_engines": {...}}); `overlay` wins when both somehow appear.
    overlay: dict | None = None
    LLM_engines: dict | None = Field(default=None)
    embedding_server: dict | None = None

    def to_overlay(self) -> dict:
        if isinstance(self.overlay, dict):
            return self.overlay
        out: dict = {}
        if self.LLM_engines is not None:
            out["LLM_engines"] = self.LLM_engines
        if self.embedding_server is not None:
            out["embedding_server"] = self.embedding_server
        return out


@router.post("/config/import", dependencies=[Depends(require_admin)])
async def import_config(
    body: ImportRequest, force: bool = False, manager: ModelManager = Depends(get_manager)
):
    """Replace the whole overlay with an uploaded backup. Validates first; refuses
    (409) if an affected instance is still running unless `force=true`."""
    try:
        summary = await manager.import_overlay(body.to_overlay(), force=force)
    except ConfigInvalid as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return {"applied": True, **summary}


# -- Version history / rollback ----------------------------------------------

def _store(request: Request):
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "store unavailable")
    return store


@router.get("/config/versions", dependencies=[Depends(require_operator)])
async def list_versions(request: Request, before: int | None = None, limit: int = 50):
    """Snapshot history (metadata only), newest first. `before` is an id cursor."""
    rows = await _store(request).list_config_versions(before=before, limit=limit)
    current_hash = overlay_blob_and_hash()[1]
    for r in rows:
        r["is_current"] = r["sha256"] == current_hash
    return {"versions": rows}


@router.get("/config/versions/{version_id}", dependencies=[Depends(require_operator)])
async def get_version(version_id: int, request: Request):
    row = await _store(request).get_config_version(version_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no version {version_id}")
    row["overlay"] = json.loads(row["overlay"])
    return row


@router.get("/config/versions/{version_id}/diff", dependencies=[Depends(require_operator)])
async def diff_version(version_id: int, request: Request, against: int | None = None):
    """Pretty-printed overlay of `version_id` vs another version (or the live
    overlay when `against` is omitted), for a side-by-side text diff in the UI."""
    store = _store(request)
    row = await store.get_config_version(version_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no version {version_id}")
    a = json.dumps(json.loads(row["overlay"]), indent=2, sort_keys=True, ensure_ascii=False)
    if against is not None:
        other = await store.get_config_version(against)
        if other is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no version {against}")
        b_overlay, b_label = json.loads(other["overlay"]), f"v{against}"
    else:
        from app.services.overlay import load_overlay
        b_overlay, b_label = load_overlay(), "current"
    b = json.dumps(b_overlay, indent=2, sort_keys=True, ensure_ascii=False)
    return {"from": {"label": f"v{version_id}", "text": a},
            "to": {"label": b_label, "text": b}}


@router.post("/config/versions/{version_id}/rollback", dependencies=[Depends(require_admin)])
async def rollback_version(
    version_id: int, request: Request, force: bool = False,
    manager: ModelManager = Depends(get_manager),
):
    """Restore a past snapshot by re-importing its overlay. Itself a mutation, so
    it produces a fresh version (history is append-only; you can roll forward)."""
    row = await _store(request).get_config_version(version_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no version {version_id}")
    try:
        summary = await manager.import_overlay(json.loads(row["overlay"]), force=force)
    except ConfigInvalid as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return {"applied": True, "rolled_back_to": version_id, **summary}

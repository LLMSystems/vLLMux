"""Observability endpoints: model event history, request usage, logs, live stream.

All read from the shared telemetry store (model_events + request_logs, the latter
written by the router) or from the in-memory registry. The route layer stays thin.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_manager
from app.api.schemas import ModelView
from app.llmops.manager import ModelManager, ModelNotFound
from app.llmops.process import read_log_head, read_log_lines
from app.llmops.state import ModelState
from app.services.vllm_metrics import parse_startup_metrics

router = APIRouter(tags=["observability"])


def _store(request: Request):
    return request.app.state.store


@router.get("/events")
async def list_events(request: Request, limit: int = 100):
    """Recent model state-transition events across all models."""
    return await _store(request).recent_events(limit=limit)


@router.get("/models/{key}/events")
async def model_events(
    key: str, request: Request, limit: int = 50, manager: ModelManager = Depends(get_manager)
):
    try:
        await manager.get(key)
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    return await _store(request).recent_events(key=key, limit=limit)


@router.get("/usage")
async def usage(request: Request, since: Optional[float] = None):
    """Per-model request aggregates (count, error rate, latency, tokens)."""
    return await _store(request).usage_summary(since=since)


@router.get("/load")
async def load(request: Request):
    """Per-group live load: ready/asleep/stopped replica counts plus queue depth
    (waiting), in-flight (running) and KV utilisation aggregated from the router
    scrape. The signal the autoscaler acts on. Empty until the first poll lands."""
    return getattr(request.app.state, "load_stats", {})


@router.get("/requests")
async def requests_log(request: Request, model_key: Optional[str] = None, limit: int = 100):
    return await _store(request).recent_requests(model_key=model_key, limit=limit)


@router.get("/models/{key}/logs")
async def model_logs(
    key: str, tail: int = 200, manager: ModelManager = Depends(get_manager)
):
    try:
        inst = await manager.get(key)
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    content = read_log_lines(inst.log_path, n=tail)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no log file for {key}")
    return {"key": key, "log_path": inst.log_path, "content": content}


@router.get("/models/{key}/metrics")
async def model_metrics(key: str, manager: ModelManager = Depends(get_manager)):
    """vLLM startup capacity/memory/compile metrics parsed from the engine log.

    Only meaningful once the instance is READY (the metrics are printed at the end
    of model loading); returns {ready: false} otherwise so the UI hides the panel."""
    try:
        inst = await manager.get(key)
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    if inst.state != ModelState.READY:
        return {"ready": False, "has_any": False}
    head = read_log_head(inst.log_path)
    if head is None:
        return {"ready": True, "has_any": False}
    return {"ready": True, **parse_startup_metrics(head)}


async def model_snapshot_stream(manager, interval: float = 1.0, heartbeat_every: float = 15.0):
    """SSE generator: emit the full model snapshot whenever it changes.

    Cheap snapshot-diff (no pub/sub bus): re-read the fleet each interval, emit on
    change, send a heartbeat if nothing changed for a while. In HA/multi-node mode
    the view comes from the shared store (each node only actuates its own models, so
    the local registry alone is incomplete — Phase 7); collapsed uses the registry.
    """
    last_sig: Optional[str] = None
    since_emit = 0.0
    while True:
        payload = sorted(
            (ModelView(**v).model_dump(mode="json")
             for v in await manager.fleet_views(prefer_store=manager.prefer_store_view())),
            key=lambda m: m["key"],
        )
        sig = json.dumps(payload, sort_keys=True, default=str)
        if sig != last_sig:
            last_sig = sig
            since_emit = 0.0
            yield f"data: {sig}\n\n"
        else:
            since_emit += interval
            if since_emit >= heartbeat_every:
                since_emit = 0.0
                yield ": heartbeat\n\n"
        await asyncio.sleep(interval)


@router.get("/stream/models")
async def stream_models(request: Request):
    """Live model state via Server-Sent Events (replaces frontend polling)."""
    return StreamingResponse(
        model_snapshot_stream(request.app.state.manager),
        media_type="text/event-stream",
    )

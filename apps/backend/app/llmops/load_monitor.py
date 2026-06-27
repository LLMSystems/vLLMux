"""Per-group live load aggregation (autoscaling Phase 1).

The router already scrapes every vLLM `/metrics` (~1s) into an in-memory cache and
exposes it at `GET {router_url}/metrics`. Rather than duplicate that scrape, the
backend polls that endpoint and joins it with its own registry lifecycle state to
produce per-group load stats — the signal the autoscaler (Phase 2) acts on and the
dashboard surfaces.

`aggregate_load` is a pure function (registry instances + router metrics -> stats)
so it is fully unit-testable without a live router.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass

from app.llmops.state import ModelKind, ModelState

logger = logging.getLogger(__name__)


@dataclass
class GroupLoad:
    group: str
    ready_replicas: int = 0
    asleep_replicas: int = 0
    stopped_replicas: int = 0
    waiting_total: float = 0.0          # sum of vllm:num_requests_waiting over ready replicas
    running_total: float = 0.0          # sum of vllm:num_requests_running over ready replicas
    waiting_per_replica: float = 0.0    # primary autoscaling signal
    kv_avg: float = 0.0                 # mean gpu_cache_usage_perc over ready replicas (0..1)


def _num(value) -> float:
    """Coerce a metric value to float; None / non-numeric (failed scrape) -> 0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def aggregate_load(instances, router_metrics: dict) -> dict[str, dict]:
    """Per-group load from registry lifecycle state + the router's metrics scrape.

    `instances` is the registry snapshot (ModelInstance); `router_metrics` is the
    router's `/metrics` JSON: {group: {instance_id: {waiting, running, kv_cache_usage_perc, ...}}}.
    Only LLM groups are aggregated. Load sums cover READY replicas only (asleep /
    stopped contribute no traffic but are counted for capacity decisions).
    """
    groups: dict[str, GroupLoad] = {}
    kv_samples: dict[str, list[float]] = {}

    for inst in instances:
        if inst.kind != ModelKind.LLM:
            continue
        group, _, instance_id = inst.key.partition("::")
        gl = groups.setdefault(group, GroupLoad(group=group))

        if inst.state == ModelState.READY:
            gl.ready_replicas += 1
            m = (router_metrics.get(group) or {}).get(instance_id) or {}
            gl.waiting_total += _num(m.get("waiting"))
            gl.running_total += _num(m.get("running"))
            kv = m.get("kv_cache_usage_perc")
            if kv is not None:
                kv_samples.setdefault(group, []).append(_num(kv))
        elif inst.state == ModelState.SLEEPING:
            gl.asleep_replicas += 1
        elif inst.state == ModelState.STOPPED:
            gl.stopped_replicas += 1

    for group, gl in groups.items():
        if gl.ready_replicas:
            gl.waiting_per_replica = gl.waiting_total / gl.ready_replicas
        samples = kv_samples.get(group)
        if samples:
            gl.kv_avg = sum(samples) / len(samples)

    return {g: asdict(gl) for g, gl in groups.items()}


async def fetch_router_metrics(http_client, router_url: str, timeout: float = 3.0) -> dict:
    """GET the router's per-instance metrics cache; {} on any error (best-effort)."""
    if not router_url:
        return {}
    try:
        resp = await http_client.get(f"{router_url}/metrics", timeout=timeout)
        if resp.status_code != 200:
            return {}
        return resp.json()
    except Exception:
        return {}


async def load_monitor_loop(app, registry, http_client, router_url: str, interval: float = 5.0) -> None:
    """Background task: refresh app.state.load_stats from the router scrape + registry."""
    app.state.load_stats = {}
    while True:
        try:
            metrics = await fetch_router_metrics(http_client, router_url)
            snapshot = await registry.snapshot()
            app.state.load_stats = aggregate_load(snapshot, metrics)
        except Exception:  # never let the loop die
            logger.exception("load monitor pass failed")
        await asyncio.sleep(interval)

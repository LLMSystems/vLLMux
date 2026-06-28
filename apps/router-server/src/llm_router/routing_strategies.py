"""Pluggable request-routing strategies.

The router proxies each OpenAI request to one backend instance of the resolved
model group. *Which* instance is chosen is a swappable policy: this module owns
the catalogue of strategies and the registry to look one up by name.

A strategy only *picks a candidate*. All the resilient-routing machinery — the
in-flight accounting, the failover loop across instances, the per-backend
cooldown — stays in the proxy (`router.py`) and is shared by every strategy.
Strategies therefore stay small and side-effect-free: given the live cluster
state they return one instance dict.

`least_load` reuses the exact score the router has always used (waiting/running/
kv-cache load + in-flight penalty + cooldown fail-open), so it is byte-for-byte
the historical behaviour and remains the default.

See docs/routing-strategies.md for the design.
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fastapi import HTTPException

from src.llm_router.backend_runtime_state import (FAIL_OPEN_PENALTY,
                                                  INFLIGHT_WEIGHT, get_inflight,
                                                  is_backend_in_cooldown,
                                                  is_draining)

logger = logging.getLogger(__name__)

DEFAULT_STRATEGY = "least_load"

# How much extra load we tolerate on an affinity "home" instance before giving up
# the cache-reuse benefit and spreading to the least-loaded replica instead.
AFFINITY_LOAD_MARGIN = float(os.environ.get("LLMOPS_AFFINITY_LOAD_MARGIN", "50.0"))


# --------------------------------------------------------------------------- #
# Scoring (shared by least_load / least_inflight / p2c / affinity fallbacks)
# --------------------------------------------------------------------------- #
def score_instance(app: Any, model_key: str, instance: dict) -> float:
    """Load-aware score for one instance; lower is less loaded.

    base load (from the ~1s metrics scrape) + in-flight penalty + cooldown
    fail-open penalty. A missing metric (cold start / just-reloaded group) counts
    as idle (0) rather than skipping the instance, so the first request after a
    start doesn't 500 — the in-flight penalty still spreads load until real
    metrics land. This mirrors the original `select_instance_least_load`.
    """
    instance_id = instance["id"]
    metric = app.state.metrics_cache.get(model_key, {}).get(instance_id)
    if metric is None:
        logger.warning(
            "No cached metrics for model=%s backend=%s; assuming idle.",
            model_key, instance_id,
        )
        base = 0.0
    else:
        base = metric.compute_load_score()
    inflight_penalty = get_inflight(app, model_key, instance_id) * INFLIGHT_WEIGHT
    cooldown_penalty = (
        FAIL_OPEN_PENALTY if is_backend_in_cooldown(app, model_key, instance_id) else 0.0
    )
    return base + inflight_penalty + cooldown_penalty


def is_instance_sleeping(app: Any, model_key: str, instance_id: str) -> bool:
    """True if the metrics poller has flagged this instance as level-1/2 asleep
    (VRAM freed, can't serve). Missing metrics -> not sleeping."""
    metric = app.state.metrics_cache.get(model_key, {}).get(instance_id)
    return bool(getattr(metric, "is_sleeping", False))


def inflight_score(app: Any, model_key: str, instance: dict) -> float:
    """Cheaper score that ignores the metrics scrape: in-flight + cooldown only."""
    instance_id = instance["id"]
    inflight_penalty = get_inflight(app, model_key, instance_id) * INFLIGHT_WEIGHT
    cooldown_penalty = (
        FAIL_OPEN_PENALTY if is_backend_in_cooldown(app, model_key, instance_id) else 0.0
    )
    return inflight_penalty + cooldown_penalty


def _least_by(score: Callable[[dict], float], candidates: list[dict]) -> dict:
    """min() that returns the first candidate on a tie (stable, like the old `<`)."""
    best = candidates[0]
    best_score = score(best)
    for inst in candidates[1:]:
        s = score(inst)
        if s < best_score:
            best, best_score = inst, s
    return best


# --------------------------------------------------------------------------- #
# Context + strategy type
# --------------------------------------------------------------------------- #
@dataclass
class SelectContext:
    app: Any
    model_key: str
    candidates: list[dict]            # eligible this request (exclude already removed)
    all_instances: list[dict] = field(default_factory=list)  # full group roster
    session_key: Optional[str] = None
    prompt_prefix: Optional[str] = None


Strategy = Callable[[SelectContext], dict]


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #
def _round_robin(ctx: SelectContext) -> dict:
    counters: dict[str, int] = ctx.app.state.rr_counters
    n = counters.get(ctx.model_key, 0)
    counters[ctx.model_key] = n + 1
    return ctx.candidates[n % len(ctx.candidates)]


def _random(ctx: SelectContext) -> dict:
    return random.choice(ctx.candidates)


def _least_inflight(ctx: SelectContext) -> dict:
    return _least_by(lambda i: inflight_score(ctx.app, ctx.model_key, i), ctx.candidates)


def _least_load(ctx: SelectContext) -> dict:
    return _least_by(lambda i: score_instance(ctx.app, ctx.model_key, i), ctx.candidates)


def _p2c(ctx: SelectContext) -> dict:
    """Power-of-two-choices: sample two distinct candidates, keep the less loaded.

    Avoids the thundering herd of everyone picking the single global-minimum during
    the ~1s window where the scrape is stale.
    """
    picks = random.sample(ctx.candidates, 2)  # caller guarantees len >= 2
    return _least_by(lambda i: score_instance(ctx.app, ctx.model_key, i), picks)


def _hash_key(key: str) -> int:
    # sha1, not builtin hash(): the latter is per-process salted, so the same key
    # would map to different replicas across workers/restarts.
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16)


def _affinity(ctx: SelectContext, key: Optional[str]) -> dict:
    """Sticky routing with a load escape valve.

    Map `key` deterministically to a home instance over the *full* roster (a stable
    set, unlike `candidates` which shrinks on failover). Keep the home replica
    unless it's missing/excluded/in cooldown or its load exceeds the least-loaded
    candidate by more than the margin — then spread like `least_load`. With no key,
    degrade straight to `least_load`, so affinity is never worse than the default.
    """
    if not key:
        return _least_load(ctx)

    roster = ctx.all_instances or ctx.candidates
    ring = sorted(roster, key=lambda i: i["id"])
    home = ring[_hash_key(key) % len(ring)]

    cand_ids = {i["id"] for i in ctx.candidates}
    best = _least_by(lambda i: score_instance(ctx.app, ctx.model_key, i), ctx.candidates)
    if (
        home["id"] in cand_ids
        and not is_backend_in_cooldown(ctx.app, ctx.model_key, home["id"])
    ):
        home_score = score_instance(ctx.app, ctx.model_key, home)
        best_score = score_instance(ctx.app, ctx.model_key, best)
        if home_score <= best_score + AFFINITY_LOAD_MARGIN:
            return home
    return best


def _session_affinity(ctx: SelectContext) -> dict:
    return _affinity(ctx, ctx.session_key)


def _prefix_affinity(ctx: SelectContext) -> dict:
    return _affinity(ctx, ctx.prompt_prefix)


STRATEGIES: dict[str, Strategy] = {
    "round_robin": _round_robin,
    "random": _random,
    "least_inflight": _least_inflight,
    "least_load": _least_load,
    "p2c": _p2c,
    "session_affinity": _session_affinity,
    "prefix_affinity": _prefix_affinity,
}


# --------------------------------------------------------------------------- #
# Public dispatcher
# --------------------------------------------------------------------------- #
async def select_instance(
    app: Any,
    model_key: str,
    model_cfg: dict,
    *,
    strategy: Optional[str] = None,
    exclude: Optional[set[str]] = None,
    session_key: Optional[str] = None,
    prompt_prefix: Optional[str] = None,
) -> dict:
    """Pick one instance for `model_key` using the named strategy.

    `exclude` is the set of instance ids already tried this request, so the proxy
    can fail over to the next-best backend without re-picking a dead one. Resilient
    machinery (in-flight, failover, cooldown) lives in the caller; this only picks.
    """
    instances = model_cfg.get("instances", [])
    if not instances:
        raise HTTPException(
            status_code=500, detail=f"Model '{model_key}' has no instances configured."
        )

    exclude = exclude or set()
    candidates = [i for i in instances if i["id"] not in exclude]
    if not candidates:
        raise HTTPException(
            status_code=503,
            detail=f"No remaining instance to try for model '{model_key}'.",
        )
    # Sleep-aware routing: a level-1-asleep instance has freed its VRAM and can't
    # serve, so drop it from the pool. If *every* candidate is asleep there is
    # nothing to serve — surface that plainly rather than routing into a failure.
    awake = [i for i in candidates if not is_instance_sleeping(app, model_key, i["id"])]
    if not awake:
        raise HTTPException(
            status_code=503,
            detail=f"All instances of model '{model_key}' are asleep.",
        )
    candidates = awake
    # Drain-aware: an instance the backend is about to stop takes no NEW requests
    # while its in-flight finish. Only drop it if a non-draining alternative
    # remains — draining is best-effort and must never hard-503 a still-live group.
    not_draining = [i for i in candidates if not is_draining(app, model_key, i["id"])]
    if not_draining:
        candidates = not_draining
    if len(candidates) == 1:
        return candidates[0]

    name = strategy or DEFAULT_STRATEGY
    fn = STRATEGIES.get(name)
    if fn is None:
        logger.warning("Unknown routing strategy %r; using %s.", name, DEFAULT_STRATEGY)
        fn = STRATEGIES[DEFAULT_STRATEGY]

    ctx = SelectContext(
        app=app,
        model_key=model_key,
        candidates=candidates,
        all_instances=instances,
        session_key=session_key,
        prompt_prefix=prompt_prefix,
    )
    chosen = fn(ctx)
    return chosen or candidates[0]

"""Per-group autoscaler (Phase 2).

Decision/action split, mirroring the rest of llmops: `decide()` is a pure function
(policy + timing -> a list of actions) and the loop applies those actions via the
ModelManager, which sets `desired` and lets the reconciler converge.

Scaling moves instances along the warm-standby ladder when the group is sleep-capable:

    stopped  --start-->  starting --> ready  --sleep-->  sleeping  --stop-->  stopped
                                  <--wake--

Signal: queue depth. **Scale up** when `waiting_per_replica` exceeds the threshold
for a sustained window (wake an asleep replica first — seconds — else cold-start a
stopped one). **Scale down** only when the whole group is idle (no running, no
waiting), so any ready replica is safe to sleep without per-instance accounting:
ready beyond `min_ready` sleep after `sleep_after_s`; asleep beyond the `min_warm`
resident floor stop after `stop_after_s`. Asymmetric (fast up, slow down) + a
post-scale-up cooldown keep it from flapping. One action per group per tick.

See docs/autoscaling-design_zh-CN.md.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.llmops.state import ModelState

logger = logging.getLogger(__name__)

# (verb, instance_key); verb in {"wake", "start", "sleep", "stop"}
Action = tuple[str, str]

_PENDING_READY = {ModelState.READY, ModelState.STARTING}  # counts toward capacity


@dataclass
class GroupTiming:
    """Per-group mutable timing state carried across ticks."""
    over_since: Optional[float] = None      # when waiting first crossed the threshold
    idle_since: Optional[float] = None       # when the group last went fully idle
    last_scale_up: float = 0.0               # for the scale-up cooldown


@dataclass
class _Inst:
    key: str
    state: ModelState


def decide(cfg, load: dict, instances: list[_Inst], sleep_capable: bool,
           timing: GroupTiming, now: float) -> list[Action]:
    """Pure policy: at most one action for the group this tick. Mutates `timing`."""
    if not cfg.enabled:
        return []

    declared = len(instances)
    max_ready = cfg.max_ready if cfg.max_ready is not None else declared
    ready = [i.key for i in instances if i.state == ModelState.READY]
    asleep = [i.key for i in instances if i.state == ModelState.SLEEPING]
    stopped = [i.key for i in instances if i.state == ModelState.STOPPED]
    effective_ready = sum(1 for i in instances if i.state in _PENDING_READY)

    # ---- Floor: always keep min_ready warm, even at zero load --------------
    # (No cooldown — bringing the serving floor up is urgent; one per tick.)
    if effective_ready < cfg.min_ready:
        if asleep:
            return [("wake", asleep[0])]
        if stopped:
            return [("start", stopped[0])]
        return []

    # ---- Scale up: sustained queue pressure -------------------------------
    if load.get("waiting_per_replica", 0.0) > cfg.scale_up_waiting:
        timing.idle_since = None
        if timing.over_since is None:
            timing.over_since = now
        sustained = now - timing.over_since >= cfg.scale_up_window_s
        cooled = now - timing.last_scale_up >= cfg.cooldown_s
        if sustained and cooled and effective_ready < max_ready:
            timing.last_scale_up = now
            timing.over_since = None
            if asleep:                       # wake is seconds; always prefer it
                return [("wake", asleep[0])]
            if stopped:
                return [("start", stopped[0])]
        return []
    timing.over_since = None

    # ---- Scale down: only when the whole group is idle ---------------------
    idle = load.get("running_total", 0.0) == 0 and load.get("waiting_total", 0.0) == 0
    if not idle:
        timing.idle_since = None
        return []
    if timing.idle_since is None:
        timing.idle_since = now
    idle_for = now - timing.idle_since

    # ready -> sleep (or stop if not sleep-capable), keeping min_ready warm
    if idle_for >= cfg.sleep_after_s and len(ready) > cfg.min_ready:
        victim = ready[-1]  # keep the lower-id "primary" replicas warm
        return [("sleep" if sleep_capable else "stop", victim)]

    # sleeping -> stop, keeping the min_warm resident (ready+asleep) floor
    resident = len(ready) + len(asleep)
    if idle_for >= cfg.stop_after_s and resident > cfg.min_warm and asleep:
        return [("stop", asleep[-1])]

    return []


class Autoscaler:
    """Holds per-group timing state and applies decisions via the ModelManager."""

    def __init__(self) -> None:
        self.timing: dict[str, GroupTiming] = {}

    async def tick(self, app, manager) -> None:
        load_stats = getattr(app.state, "load_stats", {}) or {}
        snapshot = await manager.registry.snapshot()
        config = manager.config

        # Group the registry's LLM instances by group key.
        by_group: dict[str, list[_Inst]] = {}
        for inst in snapshot:
            group, _, _ = inst.key.partition("::")
            engine = config.LLM_engines.get(group)
            if engine is None or getattr(engine, "autoscale", None) is None:
                continue
            by_group.setdefault(group, []).append(_Inst(inst.key, inst.state))

        for group, instances in by_group.items():
            engine = config.LLM_engines[group]
            cfg = engine.autoscale
            if not cfg.enabled:
                continue
            sleep_capable = any(
                getattr(getattr(i, "spec", None), "sleep_enabled", False)
                for i in snapshot if i.key.partition("::")[0] == group
            )
            timing = self.timing.setdefault(group, GroupTiming())
            load = load_stats.get(group, {})
            now = asyncio.get_event_loop().time()
            actions = decide(cfg, load, instances, sleep_capable, timing, now)
            for verb, key in actions:
                await self._apply(manager, verb, key)

    async def _apply(self, manager, verb: str, key: str) -> None:
        try:
            if verb == "wake":
                await manager.wake(key)
            elif verb == "start":
                await manager.start(key)
            elif verb == "sleep":
                await manager.sleep(key)
            elif verb == "stop":
                await manager.stop(key)
            logger.info("autoscaler: %s %s", verb, key)
        except Exception as e:
            # VRAM preflight, sleep endpoint errors, etc. are expected back-pressure —
            # log and let the next tick reassess rather than crash the loop.
            logger.info("autoscaler: %s %s skipped (%s)", verb, key, e)


async def autoscaler_loop(app, manager, interval: float = 5.0) -> None:
    """Background task: evaluate every autoscale-enabled group each interval."""
    autoscaler = Autoscaler()
    app.state.autoscaler = autoscaler
    while True:
        try:
            await autoscaler.tick(app, manager)
        except Exception:  # never let the loop die
            logger.exception("autoscaler pass failed")
        await asyncio.sleep(interval)

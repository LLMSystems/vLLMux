"""The reconciler: derives each model's observed state from ground truth.

This is the heart of the redesign. Instead of state being set by hand at scattered
call sites (and drifting), one loop periodically inspects, for every instance:
  1. process liveness  -> a managed process that exited means FAILED (or, if we
     asked it to stop, STOPPED).
  2. HTTP readiness     -> a STARTING instance whose /health turns 200 is READY;
     one that never comes up within start_timeout is FAILED.

Because observed state is *recomputed* from these two signals each tick, it can't
silently lie the way the old health-only + manual-set scheme did.

State transitions are captured as (inst, from, to, detail) tuples *under the
registry lock*, then persisted to the telemetry store *after* the lock releases,
so DB IO never extends the critical section.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

from app.core.settings import BackendSettings
from app.llmops.events import emit_transition
from app.llmops.instance import ModelInstance
from app.llmops.process import read_log_tail, terminate_process_group
from app.llmops.probes import is_ready
from app.llmops.registry import ModelRegistry
from app.llmops.state import Desired, ModelKind, ModelState

logger = logging.getLogger(__name__)

# States the reconciler actively tracks (others are terminal/idle until acted on).
# SLEEPING is deliberately excluded: a level-1-asleep instance keeps its process
# alive but must not be health-probed (its /health may still 200 yet it can't
# serve). It is left alone until a wake/stop request acts on it; only an
# unexpected process exit (caught below, before this gate) still moves it.
_LIVE_STATES = {ModelState.STARTING, ModelState.READY, ModelState.STOPPING}

# (instance, from_state, to_state, detail)
Transition = tuple[ModelInstance, ModelState, ModelState, Optional[str]]


def _maybe_schedule_restart(inst: ModelInstance, settings: BackendSettings) -> None:
    """Arm an auto-restart for a crashed/timed-out instance we still want running,
    if it's managed and within the restart budget. Exponential backoff."""
    if (
        inst.desired == Desired.RUNNING
        and inst.managed
        and settings.auto_restart
        and inst.restart_count < settings.max_restarts
    ):
        backoff = settings.restart_backoff_base * (2**inst.restart_count)
        inst.next_restart_at = time.time() + backoff


def _kill_async(proc, timeout: float) -> None:
    """Reap a process group in the background — terminate_process_group blocks on
    proc.wait, which must never run under the registry lock."""
    loop = asyncio.get_event_loop()
    fut = loop.run_in_executor(None, terminate_process_group, proc, timeout)
    fut.add_done_callback(lambda f: f.exception())  # swallow; don't leak the future


def _log_size(inst: ModelInstance) -> int:
    """Current bytes of the instance log, or the last seen size if unreadable."""
    if not inst.log_path:
        return inst.last_log_size
    try:
        return os.path.getsize(inst.log_path)
    except OSError:
        return inst.last_log_size


def _check_process_exit(inst: ModelInstance) -> Optional[Transition]:
    """If a managed process has exited, advance its state. Returns the transition."""
    if not inst.managed or inst.proc is None:
        return None
    rc = inst.proc.poll()
    if rc is None:
        return None  # still alive

    prev = inst.state
    if inst.state == ModelState.STOPPING:
        inst.set_state(ModelState.STOPPED)
        detail = None
    else:
        tail = read_log_tail(inst.log_path) if inst.log_path else ""
        detail = f"process exited (rc={rc})" + (f"\n{tail}" if tail else "")
        inst.last_error = detail
        inst.was_failed = True
        inst.set_state(ModelState.FAILED)
    inst.proc = None
    inst.pid = None
    return (inst, prev, inst.state, detail)


async def _reconcile_instance(
    inst: ModelInstance, http_client, settings: BackendSettings
) -> list[Transition]:
    # 1. Process death takes priority and is authoritative.
    exit_transition = _check_process_exit(inst)
    if exit_transition is not None:
        _, _, to_state, _ = exit_transition
        # Schedule an auto-restart for a crash (not a user-requested stop).
        if to_state == ModelState.FAILED:
            _maybe_schedule_restart(inst, settings)
        return [exit_transition]
    if inst.state not in _LIVE_STATES:
        return []

    # 2. Readiness probe for STARTING / READY (STOPPING waits on process exit).
    if inst.state in (ModelState.STARTING, ModelState.READY):
        ready = await is_ready(http_client, inst.spec.probe_url)
        if inst.state == ModelState.STARTING:
            if ready:
                inst.last_error = None
                inst.restart_count = 0  # healthy run -> fresh restart budget
                inst.next_restart_at = None
                inst.set_state(ModelState.READY)
                return [(inst, ModelState.STARTING, ModelState.READY, None)]
            # Progress-aware timeout: a growing log means the model is still
            # downloading/loading, not hung — only fail after start_timeout of *no*
            # progress, so a slow cold start isn't mistaken for a stall.
            now = time.time()
            size = _log_size(inst)
            if size > inst.last_log_size:
                inst.last_log_size = size
                inst.last_progress_at = now
            idle_since = inst.last_progress_at or inst.started_at or now
            if now - idle_since > settings.start_timeout:
                detail = (
                    f"startup timeout: no log progress for {settings.start_timeout:.0f}s "
                    "and /health never returned 200"
                )
                inst.last_error = detail
                inst.was_failed = True
                # Kill the (likely hung) process so FAILED is honest — no orphan
                # left loading and holding the GPU.
                proc = inst.proc
                inst.proc = None
                inst.pid = None
                inst.set_state(ModelState.FAILED)
                if proc is not None:
                    _kill_async(proc, settings.stop_timeout)
                _maybe_schedule_restart(inst, settings)  # recover if it was transient
                return [(inst, ModelState.STARTING, ModelState.FAILED, detail)]
        elif inst.state == ModelState.READY and not ready:
            # Don't flap to FAILED on a transient health miss; real death is
            # caught by the process-exit check. Just record it (no transition).
            inst.last_error = "health probe failing while process alive"
            inst.touch()
    return []


async def _persist(store, transitions: list[Transition], notifier=None, settings=None) -> None:
    """Funnel captured transitions through persist + alert; never breaks ops."""
    if not transitions:
        return
    for inst, frm, to, detail in transitions:
        await emit_transition(store, notifier, settings, inst, frm, to, detail)


async def _process_restarts(
    registry: ModelRegistry, settings: BackendSettings, store, manager,
    foreign: set[str],
) -> None:
    """Start any crashed-but-wanted instances whose backoff has elapsed.

    `foreign` (keys owned by another node-agent) is computed once per pass by the
    caller and excluded here — a different node restarts its own.
    """
    now = time.time()
    due: list[tuple[str, int]] = []
    async with registry.lock:
        for inst in registry.values():
            if (
                inst.state == ModelState.FAILED
                and inst.desired == Desired.RUNNING
                and inst.managed
                and inst.key not in foreign
                and inst.next_restart_at is not None
                and now >= inst.next_restart_at
                and inst.restart_count < settings.max_restarts
            ):
                inst.next_restart_at = None
                inst.restart_count += 1
                due.append((inst.key, inst.restart_count))
    for key, attempt in due:
        logger.info("Auto-restarting %s (attempt %d/%d)", key, attempt, settings.max_restarts)
        try:
            # reset_restart=False preserves the climbing count so a perpetually
            # crashing model eventually stops retrying.
            await manager.start(key, reset_restart=False)
        except Exception as e:
            logger.warning("Auto-restart of %s failed: %s", key, e)


async def _backfill_node_state(
    registry: ModelRegistry, store, settings: BackendSettings,
    transitions: list[Transition], foreign: set[str],
) -> None:
    """The node-agent's writeback to the shared store, for the instances it owns:

    - HA 3a: each READY instance's routable address (instances_live) so any router
      routes over the network; deregistered the moment it leaves READY.
    - HA 3d: every instance's full observed state (instance_observed) so any
      control-plane replica can assemble the same fleet view from the DB.

    `foreign` keys are owned by another node-agent and skipped (that agent writes
    them). On a single host nothing is foreign, so this writes the whole registry —
    behaviour unchanged. No-op when the store can't track live addresses."""
    if store is None or not hasattr(store, "upsert_instance_live"):
        return
    for inst, frm, to, _detail in transitions:
        if frm == ModelState.READY and to != ModelState.READY:
            try:
                await store.remove_instance_live(inst.key)
            except Exception:
                logger.debug("remove_instance_live failed for %s", inst.key, exc_info=True)
    node_id = settings.instance_id or None
    has_observed = hasattr(store, "upsert_instance_observed")
    for inst in registry.values():
        if inst.key in foreign:
            continue  # owned by another node-agent
        # 3a routing address: only READY instances are routable.
        if inst.state == ModelState.READY:
            host = settings.node_host or inst.host
            group, _, instance_id = inst.key.partition("::")
            try:
                await store.upsert_instance_live(
                    key=inst.key, group_key=group, instance_id=instance_id,
                    host=host, port=inst.port, ttl=settings.live_ttl,
                    node_id=node_id, state=inst.state.value,
                )
            except Exception:
                logger.debug("upsert_instance_live failed for %s", inst.key, exc_info=True)
        # 3d observed: full state for every owned instance, in any state.
        if has_observed:
            try:
                await store.upsert_instance_observed(
                    key=inst.key, node_id=node_id, state=inst.state.value,
                    view=json.dumps(inst.observed_dict()), ttl=settings.live_ttl,
                )
            except Exception:
                logger.debug("upsert_instance_observed failed for %s", inst.key, exc_info=True)
    # Sweep rows whose lease lapsed — e.g. left by a crashed/restarted agent whose
    # READY->stop transition this process never saw — so the tables show only live
    # state rather than relying on read-time filtering alone.
    if hasattr(store, "prune_instances_live"):
        try:
            await store.prune_instances_live()
        except Exception:
            logger.debug("prune_instances_live failed", exc_info=True)
    if hasattr(store, "prune_instance_observed"):
        try:
            await store.prune_instance_observed()
        except Exception:
            logger.debug("prune_instance_observed failed", exc_info=True)


async def converge_desired(
    registry: ModelRegistry, settings: BackendSettings, store, manager, foreign: set[str],
) -> None:
    """Drive every *owned* instance toward its persisted desired state — the per-node
    actuation step (HA Phase 7). Generalises boot-time `replay_desired` into a
    continuous convergence so a node actuates whatever is assigned to it, no matter
    which replica received the API/autoscaler write:

      desired=running & STOPPED        -> start   (a STOPPED-but-wanted instance, e.g.
                                                   just assigned to this node)
      desired=stopped & live           -> stop    (ready/starting/sleeping)
      desired=asleep  & READY          -> sleep
      desired=running & SLEEPING       -> wake

    FAILED + desired=running recovery is intentionally left to `_process_restarts`
    (restart budget + backoff), so this never fights the crash-loop guard. `foreign`
    keys (owned by another live node) are skipped. Collapsed single host: the one
    node owns everything, so this converges the whole fleet — same end state as the
    sync API path, just driven by the loop. Best-effort per key."""
    if store is None or manager is None or not hasattr(store, "list_instance_desired"):
        return
    try:
        desired = await store.list_instance_desired()
    except Exception:
        logger.debug("converge_desired: failed to read desired", exc_info=True)
        return

    # Snapshot (key, state) under the lock; act outside it (start/stop take the lock).
    async with registry.lock:
        states = {
            inst.key: inst.state for inst in registry.values()
        }

    for key, want in desired.items():
        if key in foreign or key not in states:
            continue
        state = states[key]
        try:
            if want == Desired.RUNNING.value and state == ModelState.STOPPED:
                await manager.start(key)
            elif want == Desired.STOPPED.value and state in (
                ModelState.READY, ModelState.STARTING, ModelState.SLEEPING
            ):
                await manager.stop(key)
            elif want == Desired.ASLEEP.value and state == ModelState.READY:
                await manager.sleep(key)
            elif want == Desired.RUNNING.value and state == ModelState.SLEEPING:
                await manager.wake(key)
        except Exception:
            logger.debug("converge_desired: action for %s (want=%s) failed", key, want, exc_info=True)


async def reconcile_once(
    registry: ModelRegistry, http_client, settings: BackendSettings, store=None,
    manager=None, notifier=None,
) -> None:
    """One reconciliation pass over every registered instance."""
    async with registry.lock:
        instances = registry.values()
        results = await asyncio.gather(
            *(_reconcile_instance(inst, http_client, settings) for inst in instances)
        )
    transitions = [t for sub in results for t in sub]
    await _persist(store, transitions, notifier, settings)
    # Compute node ownership once per pass: keys owned by another node-agent are
    # neither written back nor actuated by us. Empty on a single host.
    foreign: set[str] = set()
    if manager is not None and hasattr(manager, "foreign_assignments"):
        foreign = await manager.foreign_assignments()
    await _backfill_node_state(registry, store, settings, transitions, foreign)
    # An LLM instance that just turned READY may be a newly-added overlay
    # instance the router doesn't know about yet. Nudge it to re-read config so
    # the instance joins its load-balancing pool — only now that it's actually
    # serving, so the router never routes to a not-yet-up backend. Idempotent;
    # one reload per pass even if several instances came up together.
    if manager is not None and any(
        to == ModelState.READY and inst.kind == ModelKind.LLM
        for inst, _frm, to, _detail in transitions
    ):
        await manager.trigger_router_reload()
    # Keep the Prometheus scrape-target file in sync whenever a vLLM instance
    # joins or leaves the ready pool (READY in either direction of a transition),
    # so monitoring tracks the live fleet. Idempotent (write-if-changed).
    if manager is not None and any(
        inst.kind == ModelKind.LLM and ModelState.READY in (frm, to)
        for inst, frm, to, _detail in transitions
    ):
        await manager.write_prometheus_targets()
    if manager is not None and settings.auto_restart:
        await _process_restarts(registry, settings, store, manager, foreign)
    # Per-node actuation (HA Phase 7): converge owned instances to their persisted
    # desired state. Reuses the `foreign` set computed above. Collapsed = no-op
    # beyond what the sync API already did (idempotent).
    await converge_desired(registry, settings, store, manager, foreign)


async def adopt_running(
    registry: ModelRegistry, http_client, settings: BackendSettings, store=None, notifier=None
) -> None:
    """Boot-time adoption: mark already-healthy backends as READY (unmanaged).

    Handles the case where the backend restarted but vLLM / the embedding server
    is still up (or the router started them). Keeps state honest from t=0.
    """
    transitions: list[Transition] = []
    async with registry.lock:
        for inst in registry.values():
            if await is_ready(http_client, inst.spec.probe_url):
                prev = inst.state
                inst.managed = False
                inst.desired = Desired.RUNNING
                inst.set_state(ModelState.READY)
                logger.info("Adopted already-running instance: %s", inst.key)
                transitions.append((inst, prev, ModelState.READY, "adopted (external)"))
    await _persist(store, transitions, notifier, settings)


async def reconcile_loop(
    registry: ModelRegistry, http_client, settings: BackendSettings, store=None,
    manager=None, notifier=None,
) -> None:
    """Background task: reconcile forever at the configured interval."""
    while True:
        try:
            await reconcile_once(registry, http_client, settings, store, manager, notifier)
        except Exception:  # never let the loop die
            logger.exception("reconcile pass failed")
        await asyncio.sleep(settings.poll_interval)

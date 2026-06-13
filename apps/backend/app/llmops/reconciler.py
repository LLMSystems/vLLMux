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
import logging
import time
from typing import Optional

from app.core.settings import BackendSettings
from app.llmops.instance import ModelInstance
from app.llmops.process import read_log_tail
from app.llmops.probes import is_ready
from app.llmops.registry import ModelRegistry
from app.llmops.state import Desired, ModelState

logger = logging.getLogger(__name__)

# States the reconciler actively tracks (others are terminal/idle until acted on).
_LIVE_STATES = {ModelState.STARTING, ModelState.READY, ModelState.STOPPING}

# (instance, from_state, to_state, detail)
Transition = tuple[ModelInstance, ModelState, ModelState, Optional[str]]


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
        return [exit_transition]
    if inst.state not in _LIVE_STATES:
        return []

    # 2. Readiness probe for STARTING / READY (STOPPING waits on process exit).
    if inst.state in (ModelState.STARTING, ModelState.READY):
        ready = await is_ready(http_client, inst.spec.probe_url)
        if inst.state == ModelState.STARTING:
            if ready:
                inst.last_error = None
                inst.set_state(ModelState.READY)
                return [(inst, ModelState.STARTING, ModelState.READY, None)]
            if inst.started_at and (time.time() - inst.started_at) > settings.start_timeout:
                detail = "startup timeout: /health did not return 200 in time"
                inst.last_error = detail
                inst.set_state(ModelState.FAILED)
                return [(inst, ModelState.STARTING, ModelState.FAILED, detail)]
        elif inst.state == ModelState.READY and not ready:
            # Don't flap to FAILED on a transient health miss; real death is
            # caught by the process-exit check. Just record it (no transition).
            inst.last_error = "health probe failing while process alive"
            inst.touch()
    return []


async def _persist(store, transitions: list[Transition]) -> None:
    """Best-effort write of captured transitions; telemetry never breaks ops."""
    if store is None or not transitions:
        return
    for inst, frm, to, detail in transitions:
        try:
            await store.record_model_event(
                inst.key, inst.kind.value, frm.value, to.value, detail
            )
        except Exception:
            logger.exception("Failed to record model event for %s", inst.key)


async def reconcile_once(
    registry: ModelRegistry, http_client, settings: BackendSettings, store=None
) -> None:
    """One reconciliation pass over every registered instance."""
    async with registry.lock:
        instances = registry.values()
        results = await asyncio.gather(
            *(_reconcile_instance(inst, http_client, settings) for inst in instances)
        )
    transitions = [t for sub in results for t in sub]
    await _persist(store, transitions)


async def adopt_running(
    registry: ModelRegistry, http_client, settings: BackendSettings, store=None
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
    await _persist(store, transitions)


async def reconcile_loop(
    registry: ModelRegistry, http_client, settings: BackendSettings, store=None
) -> None:
    """Background task: reconcile forever at the configured interval."""
    while True:
        try:
            await reconcile_once(registry, http_client, settings, store)
        except Exception:  # never let the loop die
            logger.exception("reconcile pass failed")
        await asyncio.sleep(settings.poll_interval)

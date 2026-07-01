"""ModelManager: start/stop orchestration over the registry.

This is the service API the api/ layer calls. It owns *transitions a user
requests* (start, stop) and writes the resulting desired/observed state into the
registry; the reconciler owns everything that happens *after* (STARTING->READY,
death detection). Blocking OS calls are pushed to the default executor so the
event loop is never held.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from app.core.settings import BackendSettings
from app.llmops.events import emit_transition
from app.llmops.instance import ModelInstance
from app.llmops.launchers import CAP_RUNTIME_LORA, CAP_SLEEP, Launcher
from app.llmops.process import spawn_process, terminate_process_group
from app.llmops.registry import ModelRegistry
from app.llmops.state import Desired, ModelKind, ModelState

logger = logging.getLogger(__name__)


class ModelNotFound(KeyError):
    """No instance with that key exists in the config/registry."""


class ModelAlreadyRunning(RuntimeError):
    """Start requested for an instance already STARTING or READY."""


class ModelConflict(RuntimeError):
    """A create/delete request clashes with existing state (key, port, ownership)."""


class ConfigInvalid(ValueError):
    """An imported/rolled-back overlay fails schema validation (maps to 400)."""


class VRAMInsufficient(RuntimeError):
    """The target GPU likely lacks free memory for this model (pre-flight guard)."""


class GpuUnavailable(RuntimeError):
    """The pinned cuda_device doesn't exist on this host (pre-flight guard)."""


class LoraRuntimeError(RuntimeError):
    """A runtime LoRA load/unload against a vLLM instance failed."""


class SleepError(RuntimeError):
    """A sleep/wake call against a vLLM instance failed (the endpoint errored
    or is unreachable). Distinct from ModelConflict, which is a client-side
    precondition failure (wrong state / not sleep-capable)."""


def build_registry(config, config_path: str, launchers: list[Launcher]) -> ModelRegistry:
    """Enumerate every instance every launcher defines, all STOPPED initially."""
    registry = ModelRegistry()
    for launcher in launchers:
        for key in launcher.keys(config):
            spec = launcher.build_spec(config, config_path, key)
            registry.add(
                ModelInstance(
                    key=key,
                    kind=launcher.kind,
                    engine=spec.engine,
                    host=spec.host,
                    port=spec.port,
                    spec=spec,
                    model_tag=spec.model_tag,
                    log_path=spec.log_path,
                )
            )
    return registry


class ModelManager:
    def __init__(
        self,
        registry: ModelRegistry,
        launchers: list[Launcher],
        http_client,
        config,
        config_path: str,
        settings: BackendSettings,
        store=None,
        overlay_path=None,
        router_url=None,
        notifier=None,
    ) -> None:
        self.registry = registry
        # Dispatch is keyed on (kind, engine): vLLM and SGLang are both ModelKind.LLM
        # but distinct launchers. Embedding registers under ENGINE_DEFAULT.
        self._launchers: dict[tuple[ModelKind, str], Launcher] = {
            (l.kind, l.engine): l for l in launchers
        }
        self.http_client = http_client
        self.config = config
        self.config_path = config_path
        self.settings = settings
        self.store = store
        self.notifier = notifier
        self.overlay_path = overlay_path
        # Used to nudge the router to re-read config/overlay when an instance
        # becomes routable (e.g. a newly-added overlay instance turns READY).
        self.router_url = router_url.rstrip("/") if router_url else None

    def _require(self, key: str) -> ModelInstance:
        inst = self.registry.get(key)
        if inst is None:
            raise ModelNotFound(key)
        return inst

    def _launcher_for(self, inst: ModelInstance) -> Launcher:
        """The launcher that owns an instance, by its (kind, engine)."""
        return self._launchers[(inst.kind, inst.engine)]

    def _node_can_run(self, engine: str) -> bool:
        """Whether THIS node can run an engine (its image has it). Empty
        node_engines = unspecified = runs any (collapsed single host / single-engine
        deploys: always True, so the sync actuation path below is unchanged)."""
        ne = self.settings.node_engines
        return not ne or engine in ne

    async def _defer_to_owner(self, inst: ModelInstance, desired: Desired) -> ModelInstance:
        """HA Phase 7C: this node can't run `inst`'s engine, so don't actuate locally —
        just record the intent and let the scheduler place it on an engine-matching
        node, whose reconcile loop converges it. Returns the instance (state unchanged
        here; the dashboard tracks progress via observed state from the owning node)."""
        async with self.registry.lock:
            inst.desired = desired
            inst.touch()
        if self.store is not None:
            try:
                await self.store.set_instance_desired(inst.key, desired.value)
            except Exception:
                logger.warning("defer: failed to persist desired for %s", inst.key, exc_info=True)
            # Clear any assignment so the engine-aware scheduler places it fresh on a
            # node that can actually run it (it would reassign anyway, but this avoids
            # a transient wrong-node attempt).
            if desired != Desired.STOPPED and hasattr(self.store, "delete_assignment"):
                try:
                    await self.store.delete_assignment(inst.key)
                except Exception:
                    logger.debug("defer: clear assignment failed for %s", inst.key, exc_info=True)
        logger.info("Deferred %s (engine=%s) to an engine-matching node (desired=%s)",
                    inst.key, inst.engine, desired.value)
        return inst

    def _llm_engine_capabilities(self, group: str) -> frozenset:
        """Capabilities of the engine an LLM group is configured for. Callers gate
        optional features (sleep, runtime LoRA, …) on these rather than the engine
        name, so a new engine only needs to declare its capability set. Empty if the
        group / its engine's launcher is unknown."""
        engine = self.config.LLM_engines.get(group)
        if engine is None:
            return frozenset()
        engine_name = getattr(engine.settings, "engine", "vllm")
        launcher = self._launchers.get((ModelKind.LLM, engine_name))
        return launcher.capabilities if launcher else frozenset()

    async def _record(self, inst, from_state, to_state, detail=None) -> None:
        """Persist a state transition + dispatch any alert, via the shared funnel.
        Best-effort: telemetry/alerts never break ops."""
        await emit_transition(
            self.store, self.notifier, self.settings, inst, from_state, to_state, detail
        )

    async def trigger_router_reload(self) -> bool:
        """Best-effort: ask the router to re-read config + overlay so a
        newly-routable instance joins its load-balancing pool. Idempotent and
        cheap (the router just swaps an in-memory dict). Never raises — routing
        recovers on the next reload if this one is lost."""
        if not self.router_url:
            return False
        try:
            resp = await self.http_client.post(f"{self.router_url}/reload", timeout=10.0)
            return resp.status_code < 400
        except Exception:
            logger.warning("Router reload POST failed (%s/reload)", self.router_url)
            return False

    async def _drain_instance(self, key: str) -> None:
        """Best-effort graceful drain before a stop: ask the router to send this
        instance no new requests, then wait for in-flight to reach 0 (up to
        drain_timeout). Re-marks each poll so the router's drain mark can't expire
        mid-drain. Never raises — a drain failure must not block the stop."""
        if not self.router_url or self.settings.drain_timeout <= 0:
            return
        group, _, instance_id = key.partition("::")
        payload = {"model_key": group, "instance_id": instance_id,
                   "ttl": self.settings.drain_timeout + 10}
        deadline = time.monotonic() + self.settings.drain_timeout
        try:
            while True:
                resp = await self.http_client.post(
                    f"{self.router_url}/drain", json=payload, timeout=5.0
                )
                inflight = resp.json().get("inflight", 0) if resp.status_code < 400 else 0
                if inflight <= 0:
                    logger.info("Drained %s (in-flight cleared)", key)
                    return
                if time.monotonic() >= deadline:
                    logger.warning("Drain of %s timed out with %d in-flight; stopping anyway",
                                   key, inflight)
                    return
                await asyncio.sleep(self.settings.drain_poll_interval)
        except Exception:
            logger.warning("Drain of %s failed (%s/drain); stopping anyway", key, self.router_url)

    async def _persist_desired(self, key: str, desired: str) -> None:
        """Persist a user's desired state so a restart / new replica can replay it.
        Best-effort: telemetry must never break a control action."""
        if self.store is None:
            return
        try:
            await self.store.set_instance_desired(key, desired)
        except Exception:
            logger.warning("Failed to persist desired=%s for %s", desired, key, exc_info=True)
        # HA Phase 3b: claim this instance for this node when it should be live
        # (running/asleep). Collapsed = the single local node owns everything; the
        # scheduler (3c) will instead place it across nodes. Best-effort.
        if desired != Desired.STOPPED.value and hasattr(self.store, "set_assignment"):
            try:
                await self.store.set_assignment(key, self.settings.instance_id)
            except Exception:
                logger.warning("Failed to assign %s to this node", key, exc_info=True)

    async def foreign_assignments(self) -> set[str]:
        """Keys assigned to a *different, currently-alive* node — this node-agent
        must not actuate them (their owning agent does). Empty on a single host, so
        collapsed behaviour is unchanged.

        An assignment only counts as foreign if its node is alive (heartbeating in
        the nodes registry). One pointing at a vanished node — e.g. this host's own
        previous, ephemeral id after a restart, or a node that died — is reclaimable
        and NOT foreign, so actuation self-heals instead of stalling forever.
        Best-effort: any store issue yields an empty set (actuate as before)."""
        if self.store is None or not hasattr(self.store, "list_assignments"):
            return set()
        try:
            amap = await self.store.list_assignments()
        except Exception:
            return set()
        me = self.settings.instance_id
        alive: set[str] = set()
        if hasattr(self.store, "list_nodes"):
            try:
                alive = {n["node_id"] for n in await self.store.list_nodes()}
            except Exception:
                alive = set()
        return {k for k, n in amap.items() if n != me and n in alive}

    async def owning_node_api_url(self, key: str) -> Optional[str]:
        """The backend API base URL of the node that owns `key`, when that node is a
        *different*, alive node advertising an api_url — so node-local requests (logs,
        startup metrics) can be proxied to it. None when the model is local / owner
        unknown / no api_url advertised (caller then reads locally). Best-effort."""
        if self.store is None or not hasattr(self.store, "list_assignments"):
            return None
        try:
            owner = (await self.store.list_assignments()).get(key)
            if owner is None or owner == self.settings.instance_id:
                return None
            for n in await self.store.list_nodes():
                if n["node_id"] == owner:
                    url = n.get("api_url")
                    return url.rstrip("/") if url else None
        except Exception:
            logger.debug("owning_node_api_url failed for %s", key, exc_info=True)
        return None

    async def replay_desired(self) -> None:
        """On boot (after adopt_running): start instances whose persisted desired is
        RUNNING but which are currently STOPPED/FAILED — so a backend restart (or a
        replica taking over) restores what the user asked to be running. Sleeping is
        not auto-replayed (it needs a live process to sleep). Best-effort per model."""
        if self.store is None:
            return
        try:
            desired = await self.store.list_instance_desired()
        except Exception:
            logger.warning("Failed to read desired state for replay", exc_info=True)
            return
        # HA Phase 3b: don't replay instances owned by another node-agent — their
        # node restores them. Empty on a single host, so collapsed is unchanged.
        foreign = await self.foreign_assignments()
        for key, want in desired.items():
            inst = self.registry.get(key)
            if inst is None or want != Desired.RUNNING.value or key in foreign:
                continue
            if inst.state in (ModelState.STOPPED, ModelState.FAILED):
                try:
                    logger.info("Replaying desired=running for %s", key)
                    await self.start(key)
                except Exception:
                    logger.warning("Desired replay failed to start %s", key, exc_info=True)

    async def _undrain_instance(self, key: str) -> None:
        """Clear any stale drain mark when (re)starting an instance. Best-effort."""
        if not self.router_url:
            return
        group, _, instance_id = key.partition("::")
        try:
            await self.http_client.post(
                f"{self.router_url}/undrain",
                json={"model_key": group, "instance_id": instance_id}, timeout=5.0,
            )
        except Exception:
            pass

    async def write_prometheus_targets(self) -> bool:
        """Best-effort: refresh the Prometheus file_sd targets file to reflect the
        currently-ready vLLM instances. No-op unless prometheus_sd_path is set.
        Write-if-changed and never raises — monitoring discovery must never break
        the model state machine. The (blocking) file IO runs in the executor."""
        path = self.settings.prometheus_sd_path
        if not path:
            return False
        from app.services.prometheus_targets import build_targets, write_targets_file

        instances = await self.registry.snapshot()
        targets = build_targets(instances, node_host=self.settings.node_host)
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, write_targets_file, path, targets)
        except Exception:
            logger.warning("Failed to write Prometheus SD file at %s", path)
            return False

    async def list(self) -> list[ModelInstance]:
        return await self.registry.snapshot()

    def prefer_store_view(self) -> bool:
        """Whether model-state views should come from the shared store rather than
        this node's local registry. In HA/Postgres mode every node only actuates the
        models assigned to it (Phase 7), so no single registry is the full truth —
        each node backfills its *owned* observed state to the store, which is the
        only complete view. Use it on leader and follower alike. SQLite collapsed:
        one node owns everything, so the local registry is the truth (unchanged)."""
        return (self.store is not None
                and getattr(self.store, "db_url", None) is not None
                and hasattr(self.store, "list_instance_observed"))

    async def fleet_views(self, prefer_store: bool = False) -> list[dict]:
        """The fleet's model-view dicts. Leader (prefer_store=False): from the live
        local registry — identical to before. Follower (prefer_store=True): the
        shared store's observed state (the owning agents backfill it) layered over
        the local registry, so a non-leader control-plane replica reports the real
        fleet instead of its own idle registry (HA Phase 3d). Falls back to the
        registry for any instance the store hasn't backfilled."""
        node_id = self.settings.instance_id
        base: dict[str, dict] = {}
        for inst in await self.registry.snapshot():
            v = inst.observed_dict()
            v["node_id"] = node_id
            base[inst.key] = v
        if prefer_store and self.store is not None and hasattr(self.store, "list_instance_observed"):
            try:
                for v in await self.store.list_instance_observed():
                    base[v["key"]] = v
            except Exception:
                logger.warning("fleet_views: failed to read observed from store", exc_info=True)
            # Overlay the *authoritative* desired intent (instance_desired, written
            # immediately by whichever replica took the API call) over the observed
            # desired, which lags until the owning node's reconcile converges. Without
            # this the dashboard briefly sees desired=running + state=ready after a
            # stop and flickers ready->stopped. Best-effort.
            try:
                desired = await self.store.list_instance_desired()
                for key, want in desired.items():
                    if key in base:
                        base[key]["desired"] = want
            except Exception:
                logger.debug("fleet_views: failed to overlay desired", exc_info=True)
        return list(base.values())

    async def get(self, key: str) -> ModelInstance:
        return self._require(key)

    async def _vram_preflight(self, key: str, spec, *, guard: bool) -> None:
        """Auto-place an unpinned LLM on the freest GPU, and (if `guard`) block a
        start that would almost certainly OOM. Skipped for tensor-parallel (>1)
        launches, where placement spans GPUs."""
        from app.services.gpu_service import get_gpu_info

        group = key.split("::")[0]
        engine = self.config.LLM_engines.get(group)
        tp = getattr(engine.settings, "tensor_parallel_size", 1) if engine else 1
        if tp and tp > 1:
            return

        loop = asyncio.get_event_loop()
        gpus = await loop.run_in_executor(None, get_gpu_info)
        if not gpus:
            return  # no nvidia-smi / no GPUs visible -> can't assess
        by_idx = {g["index"]: g for g in gpus}

        cuda = spec.env.get("CUDA_VISIBLE_DEVICES")
        cuda_idx = int(cuda) if cuda and cuda.isdigit() else None
        if cuda_idx is None:
            freest = max(gpus, key=lambda g: g["memory_total"] - g["memory_used"])
            cuda_idx = freest["index"]
            spec.env["CUDA_VISIBLE_DEVICES"] = str(cuda_idx)
            logger.info("Auto-placed %s on GPU %d (most free VRAM)", key, cuda_idx)

        if not guard or cuda_idx not in by_idx:
            return
        util = getattr(engine.settings, "gpu_memory_utilization", None) if engine else None
        if not util:
            return
        g = by_idx[cuda_idx]
        free = g["memory_total"] - g["memory_used"]
        required = int(util * g["memory_total"])
        if required > free:
            freest = max(gpus, key=lambda x: x["memory_total"] - x["memory_used"])
            raise VRAMInsufficient(
                f"GPU {cuda_idx} has ~{free} MiB free but {key} needs ~{required} MiB "
                f"(gpu_memory_utilization={util}). Freest GPU is {freest['index']} "
                f"(~{freest['memory_total'] - freest['memory_used']} MiB free). "
                f"Start with force=true to override."
            )

    async def _gpu_exists_preflight(self, key: str, spec) -> None:
        """Block a start pinned to a cuda_device that doesn't exist on this host.

        Without this an embedding/LLM pinned to a missing GPU only fails *after*
        spawning (often after it briefly reports READY), triggering a confusing
        crash → auto-restart loop. Applies to any pinned device; unpinned LLMs
        are auto-placed later by _vram_preflight."""
        from app.services.gpu_service import get_gpu_info

        cuda = spec.env.get("CUDA_VISIBLE_DEVICES")
        if not cuda or not cuda.isdigit():
            return  # unpinned — nothing to validate here
        cuda_idx = int(cuda)
        loop = asyncio.get_event_loop()
        gpus = await loop.run_in_executor(None, get_gpu_info)
        if not gpus:
            return  # no nvidia-smi / no GPUs visible — can't assess
        indices = {g["index"] for g in gpus}
        if cuda_idx not in indices:
            raise GpuUnavailable(
                f"GPU {cuda_idx} not found for {key}; visible GPUs: {sorted(indices)}. "
                f"Fix cuda_device in config.yaml (or the model's settings)."
            )

    async def start(self, key: str, force: bool = False, reset_restart: bool = True) -> ModelInstance:
        inst = self._require(key)
        # HA Phase 7C: if this node can't run the engine, write intent and let an
        # engine-matching node actuate it (collapsed/single-engine: always can-run).
        if not self._node_can_run(inst.engine):
            return await self._defer_to_owner(inst, Desired.RUNNING)
        launcher = self._launcher_for(inst)

        # Re-resolve the spec (config may have changed) outside the lock so the
        # GPU pre-flight's nvidia-smi call never extends the critical section.
        spec = launcher.build_spec(self.config, self.config_path, key)
        await self._gpu_exists_preflight(key, spec)
        if inst.kind == ModelKind.LLM:
            await self._vram_preflight(key, spec, guard=(self.settings.vram_guard and not force))

        async with self.registry.lock:
            if inst.state in (ModelState.STARTING, ModelState.READY):
                raise ModelAlreadyRunning(key)
            prev = inst.state
            inst.spec = spec
            inst.log_path = spec.log_path
            inst.desired = Desired.RUNNING
            inst.managed = True
            inst.started_at = time.time()
            inst.ready_at = None
            inst.last_error = None
            # Fresh startup-progress window (idle-timeout, not total-elapsed).
            inst.last_log_size = 0
            inst.last_progress_at = inst.started_at
            if reset_restart:
                inst.restart_count = 0
            inst.next_restart_at = None
            inst.set_state(ModelState.STARTING)
        await self._record(inst, prev, ModelState.STARTING)

        # Spawn outside the lock — Popen returns immediately but still does IO.
        loop = asyncio.get_event_loop()
        try:
            proc = await loop.run_in_executor(None, spawn_process, spec)
        except Exception as e:
            logger.exception("Failed to spawn %s", key)
            async with self.registry.lock:
                inst.managed = False
                inst.last_error = f"spawn failed: {e}"
                inst.set_state(ModelState.FAILED)
            await self._record(inst, ModelState.STARTING, ModelState.FAILED, f"spawn failed: {e}")
            raise

        async with self.registry.lock:
            inst.proc = proc
            inst.pid = proc.pid
            inst.touch()
        # Clear any leftover drain mark so a restarted instance rejoins the pool.
        await self._undrain_instance(key)
        await self._persist_desired(key, Desired.RUNNING.value)
        logger.info("Started %s (pid=%s)", key, proc.pid)
        return inst

    async def stop(self, key: str) -> ModelInstance:
        inst = self._require(key)
        # HA Phase 7C: not our engine -> record desired=stopped; the owning node stops it.
        if not self._node_can_run(inst.engine):
            return await self._defer_to_owner(inst, Desired.STOPPED)

        async with self.registry.lock:
            prev = inst.state
            inst.desired = Desired.STOPPED
            if inst.proc is not None and inst.managed:
                proc = inst.proc
                inst.set_state(ModelState.STOPPING)
            else:
                # Nothing of ours to kill: either already down, or adopted from
                # outside (we never spawned it, so we can't reap its group).
                if not inst.managed and inst.state == ModelState.READY:
                    inst.last_error = "externally managed; backend cannot stop it"
                    new_state = None
                else:
                    inst.proc = None
                    inst.pid = None
                    inst.set_state(ModelState.STOPPED)
                    new_state = ModelState.STOPPED
                if new_state is not None and new_state != prev:
                    await self._record(inst, prev, new_state)
                await self._persist_desired(key, Desired.STOPPED.value)
                return inst
        await self._record(inst, prev, ModelState.STOPPING)

        # Graceful drain: let in-flight requests on this instance finish before we
        # kill the process (best-effort; never blocks the stop beyond drain_timeout).
        await self._drain_instance(key)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, terminate_process_group, proc, self.settings.stop_timeout
        )
        async with self.registry.lock:
            inst.proc = None
            inst.pid = None
            inst.set_state(ModelState.STOPPED)
        await self._record(inst, ModelState.STOPPING, ModelState.STOPPED)
        await self._persist_desired(key, Desired.STOPPED.value)
        logger.info("Stopped %s", key)
        return inst

    # -- Sleep / wake (level-1 warm standby) ----------------------------------

    async def sleep(self, key: str, level: int = 1) -> ModelInstance:
        """Put a READY instance into vLLM level-1 sleep: weights are paged to CPU
        RAM and VRAM is freed, but the process stays alive so wake is seconds (no
        cold start). Requires the instance to have been launched with sleep mode.

        The HTTP call runs outside the registry lock; on failure the desired
        intent is reverted so the reconciler doesn't fight a half-applied state."""
        inst = self._require(key)
        if not self._node_can_run(inst.engine):  # HA Phase 7C: owning node sleeps it
            return await self._defer_to_owner(inst, Desired.ASLEEP)
        if not getattr(inst.spec, "sleep_enabled", False):
            raise ModelConflict(
                f"{key} was not launched with sleep mode "
                f"(set enable_sleep_mode: true on the model)"
            )
        async with self.registry.lock:
            if inst.state != ModelState.READY:
                raise ModelConflict(
                    f"can only sleep a ready model; {key} is {inst.state.value}"
                )
            prev = inst.state
            inst.desired = Desired.ASLEEP
            inst.touch()

        url = f"http://{inst.host}:{inst.port}/sleep?level={level}"
        try:
            resp = await self.http_client.post(url, timeout=30.0)
            resp.raise_for_status()
        except Exception as e:
            async with self.registry.lock:
                inst.desired = Desired.RUNNING  # revert intent; still READY
                inst.touch()
            raise SleepError(f"sleep call failed for {key}: {e}")

        async with self.registry.lock:
            inst.last_error = None
            inst.set_state(ModelState.SLEEPING)
        await self._record(inst, prev, ModelState.SLEEPING, f"sleep level={level}")
        await self._persist_desired(key, Desired.ASLEEP.value)
        # Drop it from the router's pool + Prometheus scrape while asleep.
        await self.trigger_router_reload()
        await self.write_prometheus_targets()
        logger.info("Slept %s (level=%d)", key, level)
        return inst

    async def wake(self, key: str) -> ModelInstance:
        """Wake a SLEEPING instance back to READY (vLLM /wake_up reloads weights to
        GPU). Seconds, not a cold start."""
        inst = self._require(key)
        if not self._node_can_run(inst.engine):  # HA Phase 7C: owning node wakes it
            return await self._defer_to_owner(inst, Desired.RUNNING)
        async with self.registry.lock:
            if inst.state != ModelState.SLEEPING:
                raise ModelConflict(
                    f"can only wake a sleeping model; {key} is {inst.state.value}"
                )
            prev = inst.state
            inst.desired = Desired.RUNNING
            inst.touch()

        url = f"http://{inst.host}:{inst.port}/wake_up"
        try:
            resp = await self.http_client.post(url, timeout=120.0)
            resp.raise_for_status()
        except Exception as e:
            async with self.registry.lock:
                inst.desired = Desired.ASLEEP  # revert intent; still SLEEPING
                inst.touch()
            raise SleepError(f"wake call failed for {key}: {e}")

        async with self.registry.lock:
            inst.last_error = None
            inst.set_state(ModelState.READY)
        await self._record(inst, prev, ModelState.READY, "wake_up")
        await self._persist_desired(key, Desired.RUNNING.value)
        # Rejoin the router pool + Prometheus scrape now that it serves again.
        await self.trigger_router_reload()
        await self.write_prometheus_targets()
        logger.info("Woke %s", key)
        return inst

    async def set_autoscale(self, group: str, autoscale: Optional[dict]) -> Optional[dict]:
        """Set (or clear, when None) a group's autoscale policy in the overlay.

        Unlike model_config edits this does NOT require the instances stopped —
        autoscale is not a launch parameter, and an autoscaled group can't be
        stopped manually anyway, so toggling must work while it runs. Returns the
        resolved policy (validated through the schema)."""
        from app.services.overlay import build_merged_config, load_overlay, save_overlay

        if group not in self.config.LLM_engines:
            raise ModelNotFound(group)
        overlay = load_overlay(self.overlay_path)
        entry = overlay.setdefault("LLM_engines", {}).setdefault(group, {})
        if autoscale is None:
            # Explicit-off: persist enabled:false so it overrides any base config.yaml
            # policy (a bare pop would let the base re-enable it on the next merge).
            entry["autoscale"] = {"enabled": False}
        else:
            entry["autoscale"] = autoscale
        new_config = build_merged_config(self.config_path, overlay)  # validates
        save_overlay(overlay, self.overlay_path)
        self.config = new_config
        resolved = new_config.LLM_engines[group].autoscale
        logger.info("Set autoscale for %s -> enabled=%s",
                    group, resolved.enabled if resolved else False)
        return resolved.model_dump() if resolved else None

    async def set_fallback(self, group: str, fallback: Optional[list]) -> Optional[list]:
        """Set (or clear, when None/empty) a group's cross-model fallback chain in
        the overlay. Router-only and picked up on the next /reload, so no restart.
        Validates each target exists; self-references are dropped."""
        from app.services.overlay import build_merged_config, load_overlay, save_overlay

        if group not in self.config.LLM_engines:
            raise ModelNotFound(group)
        chain = [g for g in (fallback or []) if g != group]
        unknown = [g for g in chain if g not in self.config.LLM_engines]
        if unknown:
            raise ModelConflict(f"unknown fallback group(s): {', '.join(unknown)}")

        overlay = load_overlay(self.overlay_path)
        entry = overlay.setdefault("LLM_engines", {}).setdefault(group, {})
        entry["fallback"] = chain  # [] clears it
        new_config = build_merged_config(self.config_path, overlay)  # validates
        save_overlay(overlay, self.overlay_path)
        self.config = new_config
        await self.trigger_router_reload()  # router resolves the chain on reload
        logger.info("Set fallback for %s -> %s", group, chain or "(none)")
        return new_config.LLM_engines[group].fallback

    # -- Dynamic models (overlay) ---------------------------------------------

    def _used_ports(self) -> set[int]:
        return {i.port for i in self.registry.values()}

    async def create_overlay_model(self, group: str, instance: dict, model_config: dict,
                                   autoscale: Optional[dict] = None):
        """Add a user-defined LLM instance: persist to the overlay, merge it into
        the live config, and register it (STOPPED) so it shows up immediately."""
        from app.services.overlay import build_merged_config, load_overlay, save_overlay
        from app.llmops.instance import ModelInstance

        instance_id = instance.get("id")
        if not instance_id:
            raise ModelConflict("instance id is required")
        key = f"{group}::{instance_id}"
        if self.registry.get(key) is not None:
            raise ModelConflict(f"model already exists: {key}")
        if instance.get("port") in self._used_ports():
            raise ModelConflict(f"port {instance.get('port')} is already in use")

        overlay = load_overlay(self.overlay_path)
        engines = overlay.setdefault("LLM_engines", {})
        if group in self.config.LLM_engines or group in engines:
            entry = engines.setdefault(group, {"instances": []})
            entry.setdefault("instances", []).append(instance)
        else:
            engines[group] = {"instances": [instance], "model_config": model_config}
        if autoscale is not None:
            engines[group]["autoscale"] = autoscale

        # Validate the merged result before persisting anything.
        new_config = build_merged_config(self.config_path, overlay)
        save_overlay(overlay, self.overlay_path)
        self.config = new_config

        group_engine = getattr(new_config.LLM_engines[group].settings, "engine", "vllm")
        launcher = self._launchers.get((ModelKind.LLM, group_engine))
        if launcher is None:
            raise ModelConflict(f"unsupported engine '{group_engine}' (no launcher registered)")
        spec = launcher.build_spec(self.config, self.config_path, key)
        async with self.registry.lock:
            self.registry.add(
                ModelInstance(
                    key=key,
                    kind=ModelKind.LLM,
                    engine=spec.engine,
                    host=spec.host,
                    port=spec.port,
                    spec=spec,
                    model_tag=spec.model_tag,
                    log_path=spec.log_path,
                )
            )
        inst = self._require(key)
        await self._record(inst, None, ModelState.STOPPED, "added via dashboard")
        logger.info("Created dynamic model %s", key)
        return inst

    async def update_overlay_model(self, key: str, instance: dict, model_config: dict):
        """Edit a model's instance fields and/or shared model_config, persisting
        the change as an overlay override (config.yaml is never rewritten).

        Works for both dynamic and config.yaml-defined LLM models; the latter's
        edits are layered on as deltas. The model must be stopped — parameters
        only take effect on the next launch. Editing model_config affects every
        instance in the group."""
        from app.services.overlay import build_merged_config, load_overlay, save_overlay
        from app.llmops.state import ModelKind

        inst = self._require(key)
        if inst.kind != ModelKind.LLM:
            raise ModelConflict("only LLM models are editable")
        if inst.state not in (ModelState.STOPPED, ModelState.FAILED):
            raise ModelConflict(f"stop {key} before editing it")
        if not model_config.get("model_tag"):
            raise ModelConflict("model_tag is required")

        group, _, instance_id = key.partition("::")
        instance = dict(instance)
        instance["id"] = instance_id  # id is the key — never editable

        new_port = instance.get("port")
        if new_port in (self._used_ports() - {inst.port}):
            raise ModelConflict(f"port {new_port} is already in use")

        overlay = load_overlay(self.overlay_path)
        entry = overlay.setdefault("LLM_engines", {}).setdefault(group, {"instances": []})
        instances = entry.setdefault("instances", [])
        for n, i in enumerate(instances):
            if i.get("id") == instance_id:
                instances[n] = instance
                break
        else:
            instances.append(instance)
        entry["model_config"] = model_config

        # Validate the merged result before persisting anything.
        new_config = build_merged_config(self.config_path, overlay)
        save_overlay(overlay, self.overlay_path)
        self.config = new_config

        # Re-resolve the spec so the next start uses the edited values. The edit may
        # have changed the group's engine, so pick the launcher by the new config.
        group_engine = getattr(new_config.LLM_engines[group].settings, "engine", "vllm")
        launcher = self._launchers.get((ModelKind.LLM, group_engine))
        if launcher is None:
            raise ModelConflict(f"unsupported engine '{group_engine}' (no launcher registered)")
        spec = launcher.build_spec(self.config, self.config_path, key)
        async with self.registry.lock:
            inst.engine = spec.engine
            inst.host = spec.host
            inst.port = spec.port
            inst.spec = spec
            inst.model_tag = spec.model_tag
            inst.log_path = spec.log_path
            inst.touch()
        await self._record(inst, None, inst.state, "edited via dashboard")
        logger.info("Updated model %s", key)
        return inst

    # ---- Runtime (hot) LoRA load / unload -------------------------------------

    async def _ready_lora_targets(self, group: str) -> list[ModelInstance]:
        """Ready LLM instances of a group — the ones to fan a LoRA op out to."""
        snap = await self.registry.snapshot()
        return [
            i for i in snap
            if i.key.split("::")[0] == group
            and i.kind == ModelKind.LLM
            and i.state == ModelState.READY
        ]

    async def _post_lora(self, inst: ModelInstance, action: str, payload: dict) -> None:
        """POST {load,unload}_lora_adapter to one instance; raise on failure. The
        path differs by engine: vLLM serves /v1/<action>_lora_adapter, SGLang serves
        /<action>_lora_adapter (no /v1). The JSON body is the same for both."""
        prefix = "" if getattr(inst, "engine", "vllm") == "sglang" else "/v1"
        url = f"http://{inst.host}:{inst.port}{prefix}/{action}_lora_adapter"
        resp = await self.http_client.post(url, json=payload, timeout=120.0)
        if resp.status_code >= 400:
            try:
                detail = resp.text
            except Exception:
                detail = ""
            raise LoraRuntimeError(f"{inst.key}: {resp.status_code} {detail[:200]}")

    def _persist_lora(self, group: str, name: str, path: str | None,
                      base_model_name: str | None, *, remove: bool) -> None:
        """Add/remove a lora_modules entry in the overlay's model_config for the
        group, then rebuild self.config. Mirrors update_overlay_model's persistence
        so the change survives a restart (re-mounted statically) and a router
        /reload makes it routable."""
        from app.services.overlay import build_merged_config, load_overlay, save_overlay

        engine = self.config.LLM_engines[group]
        model_config = engine.settings.model_dump()
        loras = [dict(m) for m in (model_config.get("lora_modules") or [])]
        loras = [m for m in loras if m.get("name") != name]  # drop any same-name first
        if not remove:
            entry = {"name": name, "path": path}
            if base_model_name:
                entry["base_model_name"] = base_model_name
            loras.append(entry)
        model_config["lora_modules"] = loras

        overlay = load_overlay(self.overlay_path)
        group_entry = overlay.setdefault("LLM_engines", {}).setdefault(group, {})
        group_entry["model_config"] = model_config  # keep any existing instances
        new_config = build_merged_config(self.config_path, overlay)
        save_overlay(overlay, self.overlay_path)
        self.config = new_config

    async def load_lora(self, group: str, name: str, path: str,
                        base_model_name: str | None = None) -> dict:
        """Hot-load a LoRA into every ready instance of a group, then persist it.

        All-or-nothing across instances: if any instance fails, the ones that
        already loaded are rolled back so the group never ends up inconsistent
        (which would make the load-balancer 404 on some requests)."""
        engine = self.config.LLM_engines.get(group)
        if engine is None:
            raise ModelNotFound(group)
        if CAP_RUNTIME_LORA not in self._llm_engine_capabilities(group):
            engine_name = getattr(engine.settings, "engine", "vllm")
            raise ModelConflict(
                f"{group}'s engine ({engine_name}) does not support runtime LoRA"
            )
        if not getattr(engine.settings, "allow_runtime_lora", False):
            raise ModelConflict(
                f"{group} was not started with runtime LoRA updating — enable "
                "allow_runtime_lora (+ enable_lora) on the model and restart it first"
            )
        if not name or not path:
            raise ModelConflict("lora name and path are required")
        targets = await self._ready_lora_targets(group)
        if not targets:
            raise ModelConflict(f"no running instance for {group} — start it first")

        payload = {"lora_name": name, "lora_path": path}
        done: list[ModelInstance] = []
        try:
            for inst in targets:
                await self._post_lora(inst, "load", payload)
                done.append(inst)
        except LoraRuntimeError:
            for inst in done:  # roll back to keep the group consistent
                try:
                    await self._post_lora(inst, "unload", {"lora_name": name})
                except Exception:
                    logger.warning("LoRA rollback unload failed on %s", inst.key)
            raise

        self._persist_lora(group, name, path, base_model_name, remove=False)
        logger.info("Hot-loaded LoRA %s onto %s (%d instances)", name, group, len(targets))
        return {"group": group, "name": name, "instances": [i.key for i in targets]}

    async def unload_lora(self, group: str, name: str) -> dict:
        """Hot-unload a LoRA from every ready instance and drop it from the
        overlay. Best-effort across instances — a missing adapter on one isn't
        fatal, since the goal is for it to be gone everywhere."""
        engine = self.config.LLM_engines.get(group)
        if engine is None:
            raise ModelNotFound(group)
        targets = await self._ready_lora_targets(group)
        errors: list[str] = []
        for inst in targets:
            try:
                await self._post_lora(inst, "unload", {"lora_name": name})
            except Exception as e:
                errors.append(str(e))
        self._persist_lora(group, name, None, None, remove=True)
        logger.info("Hot-unloaded LoRA %s from %s", name, group)
        return {"group": group, "name": name, "instances": [i.key for i in targets], "errors": errors}

    async def update_embedding_model(self, model_type: str, name: str, settings: dict) -> str:
        """Edit an embedding/reranking model's params, persisted as an overlay
        override (config.yaml untouched). Applies on the embedding server's next
        launch, so it must be stopped. `model_type` is 'embedding' | 'reranking'."""
        from app.services.overlay import build_merged_config, load_overlay, save_overlay
        from app.llmops.launchers import EMBEDDING_KEY

        if model_type not in ("embedding", "reranking"):
            raise ModelConflict("model_type must be 'embedding' or 'reranking'")
        field = "embedding_models" if model_type == "embedding" else "reranking_models"

        emb = self.config.embedding_server
        base_models = getattr(emb, field) if emb else {}
        if name not in base_models:
            raise ModelNotFound(f"unknown {model_type} model: {name}")

        inst = self.registry.get(EMBEDDING_KEY)
        if inst is not None and inst.state not in (ModelState.STOPPED, ModelState.FAILED):
            raise ModelConflict("stop the embedding server before editing its models")

        overlay = load_overlay(self.overlay_path)
        models_ov = overlay.setdefault("embedding_server", {}).setdefault(field, {})
        merged = {**models_ov.get(name, {}), **settings}
        models_ov[name] = merged

        # Validate the merged result before persisting anything.
        new_config = build_merged_config(self.config_path, overlay)
        save_overlay(overlay, self.overlay_path)
        self.config = new_config
        logger.info("Updated %s model %s", model_type, name)
        return name

    async def delete_overlay_model(self, key: str) -> None:
        """Remove a dynamically-added model. Only overlay-owned, stopped models."""
        from app.services.overlay import build_merged_config, load_overlay, overlay_owns, save_overlay

        inst = self._require(key)
        group, _, instance_id = key.partition("::")
        overlay = load_overlay(self.overlay_path)
        if not overlay_owns(overlay, group, instance_id):
            raise ModelConflict(f"{key} is defined in config.yaml; remove it there")
        if inst.state not in (ModelState.STOPPED, ModelState.FAILED):
            raise ModelConflict(f"stop {key} before removing it")

        entry = overlay["LLM_engines"].get(group, {})
        entry["instances"] = [i for i in entry.get("instances", []) if i.get("id") != instance_id]
        if not entry.get("instances"):
            overlay["LLM_engines"].pop(group, None)
        save_overlay(overlay, self.overlay_path)
        self.config = build_merged_config(self.config_path, overlay)
        async with self.registry.lock:
            self.registry.remove(key)
        if self.store is not None:
            try:
                await self.store.delete_instance_desired(key)
            except Exception:
                logger.warning("Failed to clear desired for deleted %s", key, exc_info=True)
            if hasattr(self.store, "delete_assignment"):
                try:
                    await self.store.delete_assignment(key)
                except Exception:
                    logger.warning("Failed to clear assignment for deleted %s", key, exc_info=True)
        logger.info("Deleted dynamic model %s", key)

    # -- Config export / import (versioning) ----------------------------------

    def _resolve_specs(self, config) -> dict[str, tuple]:
        """Resolve (kind, LaunchSpec) for every instance key `config` defines."""
        out: dict[str, tuple] = {}
        for launcher in self._launchers.values():
            for key in launcher.keys(config):
                out[key] = (launcher.kind, launcher.build_spec(config, self.config_path, key))
        return out

    def export_overlay(self) -> dict:
        """The full current overlay (the entire mutable state), for backup."""
        from app.services.overlay import load_overlay

        return load_overlay(self.overlay_path)

    def resync_registry(self, new_config) -> dict[str, list[str]]:
        """Align the registry to `new_config`: add new keys (STOPPED), drop gone
        ones, refresh the spec of changed ones. Returns {added, removed, changed}.
        Caller must hold registry.lock and have ensured no affected instance is
        running (launch-param changes only take effect on the next launch)."""
        desired = self._resolve_specs(new_config)
        current = set(self.registry.keys())
        added = sorted(set(desired) - current)
        removed = sorted(current - set(desired))
        changed = sorted(
            k for k in set(desired) & current
            if self.registry.get(k).spec != desired[k][1]
        )
        for key in removed:
            self.registry.remove(key)
        for key in added:
            kind, spec = desired[key]
            self.registry.add(ModelInstance(
                key=key, kind=kind, engine=spec.engine, host=spec.host, port=spec.port,
                spec=spec, model_tag=spec.model_tag, log_path=spec.log_path,
            ))
        for key in changed:
            kind, spec = desired[key]
            inst = self.registry.get(key)
            inst.spec, inst.host, inst.port = spec, spec.host, spec.port
            inst.engine = spec.engine
            inst.model_tag, inst.log_path = spec.model_tag, spec.log_path
        return {"added": added, "removed": removed, "changed": changed}

    async def import_overlay(
        self, overlay: dict, *, force: bool = False,
        actor: Optional[str] = None, role: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> dict[str, list[str]]:
        """Replace the whole overlay with `overlay` (a backup or a past version).

        Validates the merged result first; then unless `force`, refuses if any
        instance whose definition would be removed or changed is still running
        (a launch-param change under a live process would silently drift). With
        `force`, those instances are stopped first so nothing is orphaned.
        Returns the {added, removed, changed} key summary.

        `actor`/`role`/`summary` attribute the resulting config-version snapshot
        (see below)."""
        from app.services.overlay import build_merged_config, save_overlay

        if not isinstance(overlay, dict):
            raise ConfigInvalid("overlay must be a JSON object")
        overlay = dict(overlay)
        overlay.setdefault("LLM_engines", {})
        try:
            new_config = build_merged_config(self.config_path, overlay)  # schema-validates
        except Exception as e:
            raise ConfigInvalid(f"invalid config: {e}") from e

        desired = self._resolve_specs(new_config)
        current = set(self.registry.keys())
        affected = (current - set(desired)) | {
            k for k in set(desired) & current if self.registry.get(k).spec != desired[k][1]
        }
        live = lambda k: (i := self.registry.get(k)) and i.state not in (
            ModelState.STOPPED, ModelState.FAILED)
        busy = sorted(k for k in affected if live(k))
        if busy and not force:
            raise ModelConflict(
                "stop these instances before importing (or use force): " + ", ".join(busy))
        for key in busy:  # force: stop so we don't change params under / orphan a live process
            try:
                await self.stop(key)
            except Exception:
                logger.warning("import_overlay: failed to stop %s before applying", key)

        save_overlay(overlay, self.overlay_path)
        self.config = new_config
        # HA: persist this overlay to the shared DB as the new current snapshot
        # BEFORE reloading the router. The backend and router share the overlay
        # file, and the router's /reload re-hydrates that file from the DB's latest
        # snapshot — so if the DB still held the *previous* overlay, the reload would
        # immediately clobber this import (and the request middleware, seeing the
        # file revert, would never snapshot it). No-op in SQLite mode, where the
        # file is the source of truth and the middleware snapshots it post-request.
        if getattr(self.store, "db_url", None) is not None:
            try:
                from app.core.config_versioning import snapshot_overlay
                await snapshot_overlay(self.store, actor=actor, role=role,
                                       summary=summary, path=self.overlay_path)
            except Exception:
                logger.warning("import_overlay: failed to snapshot overlay to DB", exc_info=True)
        async with self.registry.lock:
            summary_keys = self.resync_registry(new_config)
        await self.trigger_router_reload()
        logger.info("Imported overlay: %s", summary_keys)
        return summary_keys

    async def sync_overlay_from_store(self) -> bool:
        """HA Phase 7: pull the fleet's current overlay from the shared store and
        align this node's registry to it, so a model added/edited on *any* replica
        appears here too (and an engine-matching node can then actuate it). No-op
        outside Postgres mode (SQLite's file is already the single shared truth) and
        when nothing changed. Best-effort: never raises. Returns True if it resynced.

        Only the registry alignment for *non-running-here* keys matters in practice:
        a follower adds the new STOPPED instance, then converge_desired starts it if
        it's assigned here. resync_registry refuses to change a key running locally,
        which is the safe behaviour (the owning node edits its own)."""
        if self.store is None or getattr(self.store, "db_url", None) is None:
            return False
        from app.services.overlay import (build_merged_config,
                                          hydrate_overlay_from_store, load_overlay)
        try:
            changed = await hydrate_overlay_from_store(self.store, self.overlay_path)
            if not changed:
                return False
            new_config = build_merged_config(self.config_path, load_overlay(self.overlay_path))
        except Exception:
            logger.debug("sync_overlay_from_store: hydrate/merge failed", exc_info=True)
            return False
        async with self.registry.lock:
            try:
                summary = self.resync_registry(new_config)
            except Exception:
                logger.debug("sync_overlay_from_store: resync failed", exc_info=True)
                return False
            self.config = new_config
        if any(summary.values()):
            logger.info("Synced overlay from store: %s", summary)
            await self.trigger_router_reload()
            return True
        return False

    async def stop_all(self) -> None:
        """Best-effort shutdown of every managed process (used at app shutdown)."""
        for inst in self.registry.values():
            if inst.managed and inst.proc is not None and inst.proc.poll() is None:
                try:
                    terminate_process_group(inst.proc, self.settings.stop_timeout)
                except Exception:
                    logger.exception("Failed to stop %s during shutdown", inst.key)

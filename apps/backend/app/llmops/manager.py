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

from app.core.settings import BackendSettings
from app.llmops.instance import ModelInstance
from app.llmops.launchers import Launcher
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
    ) -> None:
        self.registry = registry
        self._launchers: dict[ModelKind, Launcher] = {l.kind: l for l in launchers}
        self.http_client = http_client
        self.config = config
        self.config_path = config_path
        self.settings = settings
        self.store = store
        self.overlay_path = overlay_path

    def _require(self, key: str) -> ModelInstance:
        inst = self.registry.get(key)
        if inst is None:
            raise ModelNotFound(key)
        return inst

    async def _record(self, inst, from_state, to_state, detail=None) -> None:
        """Persist a state transition. Best-effort: telemetry never breaks ops."""
        if self.store is None:
            return
        try:
            await self.store.record_model_event(
                inst.key,
                inst.kind.value,
                from_state.value if from_state else None,
                to_state.value,
                detail,
            )
        except Exception:
            logger.exception("Failed to record model event for %s", inst.key)

    async def list(self) -> list[ModelInstance]:
        return await self.registry.snapshot()

    async def get(self, key: str) -> ModelInstance:
        return self._require(key)

    async def start(self, key: str) -> ModelInstance:
        inst = self._require(key)
        launcher = self._launchers[inst.kind]

        async with self.registry.lock:
            if inst.state in (ModelState.STARTING, ModelState.READY):
                raise ModelAlreadyRunning(key)
            prev = inst.state
            # Re-resolve the spec in case config changed since boot.
            spec = launcher.build_spec(self.config, self.config_path, key)
            inst.spec = spec
            inst.log_path = spec.log_path
            inst.desired = Desired.RUNNING
            inst.managed = True
            inst.started_at = time.time()
            inst.ready_at = None
            inst.last_error = None
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
        logger.info("Started %s (pid=%s)", key, proc.pid)
        return inst

    async def stop(self, key: str) -> ModelInstance:
        inst = self._require(key)

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
                return inst
        await self._record(inst, prev, ModelState.STOPPING)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, terminate_process_group, proc, self.settings.stop_timeout
        )
        async with self.registry.lock:
            inst.proc = None
            inst.pid = None
            inst.set_state(ModelState.STOPPED)
        await self._record(inst, ModelState.STOPPING, ModelState.STOPPED)
        logger.info("Stopped %s", key)
        return inst

    # -- Dynamic models (overlay) ---------------------------------------------

    def _used_ports(self) -> set[int]:
        return {i.port for i in self.registry.values()}

    async def create_overlay_model(self, group: str, instance: dict, model_config: dict):
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

        # Validate the merged result before persisting anything.
        new_config = build_merged_config(self.config_path, overlay)
        save_overlay(overlay, self.overlay_path)
        self.config = new_config

        launcher = self._launchers[ModelKind.LLM]
        spec = launcher.build_spec(self.config, self.config_path, key)
        async with self.registry.lock:
            self.registry.add(
                ModelInstance(
                    key=key,
                    kind=ModelKind.LLM,
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
        logger.info("Deleted dynamic model %s", key)

    async def stop_all(self) -> None:
        """Best-effort shutdown of every managed process (used at app shutdown)."""
        for inst in self.registry.values():
            if inst.managed and inst.proc is not None and inst.proc.poll() is None:
                try:
                    terminate_process_group(inst.proc, self.settings.stop_timeout)
                except Exception:
                    logger.exception("Failed to stop %s during shutdown", inst.key)

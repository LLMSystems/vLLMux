"""ModelRegistry — the single source of truth for model state.

Replaces the old `app.state.running_llm_procs` (dict) + `app.state.starting_models`
(set) pair, which were maintained by hand in scattered places and drifted out of
sync. Every read and write goes through one lock-guarded dict here, and the
observed state on each record is owned exclusively by the reconciler.

The backend runs as a single uvicorn process, so an asyncio.Lock is sufficient
to serialise mutations (e.g. to prevent a double-start race).
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.llmops.instance import ModelInstance


class ModelRegistry:
    def __init__(self) -> None:
        self._instances: dict[str, ModelInstance] = {}
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        """Exposed so the reconciler/manager can hold it across read+mutate."""
        return self._lock

    def add(self, instance: ModelInstance) -> None:
        """Register an instance. Not locked — call only during boot setup."""
        self._instances[instance.key] = instance

    def remove(self, key: str) -> None:
        """Drop an instance (used when deleting a dynamically-added model)."""
        self._instances.pop(key, None)

    def get(self, key: str) -> Optional[ModelInstance]:
        return self._instances.get(key)

    def keys(self) -> list[str]:
        return list(self._instances.keys())

    def values(self) -> list[ModelInstance]:
        """Live records (not copies). Caller must hold the lock to mutate."""
        return list(self._instances.values())

    async def snapshot(self) -> list[ModelInstance]:
        """A consistent point-in-time view for read endpoints."""
        async with self._lock:
            return list(self._instances.values())

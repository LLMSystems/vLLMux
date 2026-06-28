"""Node-agent: the per-host actuator (HA Phase 3b).

A node-agent owns the processes on one GPU host: it heartbeats the node's identity
+ capacity into the shared store so the scheduler can place instances on it, and
(in later sub-phases) spawns/reaps the instances assigned to it and writes their
observed state back. On a single host the agent is *collapsed* into the backend
process — same registry, same reconciler, vLLM on localhost — so behaviour is
byte-for-byte identical to before; only multi-node deploys run a standalone agent.

Phase 3b-1 (this file) is the foundation: just the node-registration heartbeat.
The spawn/reconcile actuation is moved here in later sub-phases.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Optional

from app.core.settings import BackendSettings
from app.services.gpu_service import get_gpu_info

logger = logging.getLogger(__name__)


class NodeAgent:
    """Registers this node and keeps its heartbeat + capacity fresh in the store."""

    def __init__(self, store, settings: BackendSettings) -> None:
        self.store = store
        self.settings = settings
        self.node_id = settings.instance_id
        self.hostname = socket.gethostname()

    def _capacity(self) -> Optional[str]:
        """Current GPU inventory as compact JSON, or None if unavailable.

        Best-effort: nvidia-smi missing / no GPUs yields None rather than raising,
        so a CPU-only or sandboxed node still heartbeats (just with no capacity)."""
        try:
            gpus = get_gpu_info()
        except Exception:
            return None
        if not gpus:
            return None
        slim = [
            {"index": g["index"], "name": g["name"],
             "memory_total": g["memory_total"], "memory_used": g["memory_used"]}
            for g in gpus
        ]
        return json.dumps(slim)

    async def heartbeat_once(self) -> None:
        """Register/refresh this node. No-op if the store can't track nodes."""
        if self.store is None or not hasattr(self.store, "upsert_node"):
            return
        await self.store.upsert_node(
            self.node_id, self.hostname, self._capacity(), ttl=self.settings.node_ttl
        )
        # Housekeeping so a vanished peer's row doesn't linger past its lease.
        if hasattr(self.store, "prune_nodes"):
            try:
                await self.store.prune_nodes()
            except Exception:
                logger.debug("prune_nodes failed", exc_info=True)

    async def run(self) -> None:
        """Heartbeat forever. Runs on every node-agent (not leader-gated) — each
        host must announce itself regardless of who holds the scheduler lease."""
        if self.store is None or not hasattr(self.store, "upsert_node"):
            logger.info("Node-agent heartbeat disabled (store has no node registry)")
            return
        logger.info("Node-agent heartbeat started: node_id=%s", self.node_id)
        while True:
            try:
                await self.heartbeat_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("node-agent heartbeat failed", exc_info=True)
            await asyncio.sleep(self.settings.node_heartbeat_interval)

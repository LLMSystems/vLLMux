"""Cross-node placement scheduler (HA Phase 3c).

The leader (scheduler) decides *which node* runs each desired instance and writes
the assignment; the owning node-agent actuates it. Placement is greedy by free
VRAM. An instance whose assigned node has died (heartbeat lapsed) is replaced onto
a live node — node-failure failover.

On a single host this is a no-op: every desired instance is already assigned to the
one node by the manager, and there is nowhere else to place it.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def node_free_vram(node: dict) -> int:
    """Sum of free VRAM (MiB) across a node's GPUs, parsed from its capacity JSON.
    Unknown / no-GPU nodes report 0 (still placeable, just least-preferred)."""
    cap = node.get("capacity")
    if not cap:
        return 0
    try:
        gpus = json.loads(cap)
    except (json.JSONDecodeError, TypeError):
        return 0
    return sum(max(0, g.get("memory_total", 0) - g.get("memory_used", 0)) for g in gpus)


def node_supports(node: dict, engine: str) -> bool:
    """Whether a node can run a given engine. A node that doesn't advertise engines
    (engines NULL/empty) is unspecified and accepts any engine — so collapsed single
    host and pre-Phase-7 deploys behave exactly as before."""
    raw = node.get("engines")
    if not raw:
        return True
    try:
        return engine in json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return True


def place(
    desired: set[str], nodes: list[dict], assignments: dict[str, str],
    key_engines: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Decide assignments to (re)write. For every desired-running instance, pick the
    emptiest live node that can run the instance's engine — unless it's already on a
    live, engine-matching node (no churn). Returns only the *changes* {key: node}.

    `key_engines` maps key -> engine (default "vllm"). A key whose engine no live
    node supports is left unassigned until a matching node appears — and one sitting
    on a non-matching node (e.g. started on the wrong backend) is moved to a matching
    one. `nodes` is the set of alive nodes (node_id + capacity + engines).
    """
    if not nodes:
        return {}  # nowhere to place; leave as-is until a node appears
    key_engines = key_engines or {}
    by_id = {n["node_id"]: n for n in nodes}
    changes: dict[str, str] = {}
    for key in desired:
        engine = key_engines.get(key, "vllm")
        candidates = [n for n in nodes if node_supports(n, engine)]
        if not candidates:
            continue  # no live node can run this engine; leave unassigned
        cur_node = by_id.get(assignments.get(key))
        # Keep the current placement only if its node is alive AND engine-matching.
        if cur_node is not None and node_supports(cur_node, engine):
            continue
        # Greedy: emptiest matching node; stable tiebreak on node_id.
        target = sorted(candidates, key=lambda n: (-node_free_vram(n), n["node_id"]))[0]
        changes[key] = target["node_id"]
    return changes


class Scheduler:
    """Leader-only loop: keep desired instances placed on live, engine-matching nodes.

    `registry` (optional) resolves each instance's engine so placement can match it
    to a node that can run it. Without it, engines default to "vllm" — fine for a
    single-engine fleet."""

    def __init__(self, registry=None) -> None:
        self.registry = registry

    def _key_engines(self) -> dict[str, str]:
        if self.registry is None:
            return {}
        return {inst.key: getattr(inst, "engine", "vllm") for inst in self.registry.values()}

    async def reschedule_once(self, store, settings) -> dict[str, str]:
        """One placement pass. Reads desired-running instances, live nodes and
        current assignments from the store; writes any assignment changes. Returns
        the changes applied (empty on a single host / when nothing moved)."""
        if store is None or not hasattr(store, "list_assignments"):
            return {}
        try:
            desired_map = await store.list_instance_desired()
            nodes = await store.list_nodes()
            assignments = await store.list_assignments()
        except Exception:
            logger.warning("scheduler: failed to read placement inputs", exc_info=True)
            return {}
        from app.llmops.state import Desired

        desired = {k for k, v in desired_map.items() if v == Desired.RUNNING.value}
        changes = place(desired, nodes, assignments, self._key_engines())
        for key, node_id in changes.items():
            try:
                await store.set_assignment(key, node_id)
                logger.info("scheduler: placed %s on %s", key, node_id)
            except Exception:
                logger.warning("scheduler: failed to assign %s", key, exc_info=True)
        return changes

    async def run(self, store, settings, interval: float) -> None:
        import asyncio

        logger.info("Scheduler loop started (leader)")
        while True:
            try:
                await self.reschedule_once(store, settings)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("scheduler pass failed", exc_info=True)
            await asyncio.sleep(interval)

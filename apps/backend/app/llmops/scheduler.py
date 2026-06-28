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


def place(
    desired: set[str], nodes: list[dict], assignments: dict[str, str],
) -> dict[str, str]:
    """Decide assignments to (re)write. For every desired-running instance whose
    current assignment is missing or points to a node not in `nodes` (dead), pick
    the live node with the most free VRAM. Returns only the *changes* {key: node};
    instances already on a live node are left where they are (no churn).

    `nodes` is the set of currently-alive nodes (each a row with node_id+capacity).
    """
    alive = {n["node_id"] for n in nodes}
    if not alive:
        return {}  # nowhere to place; leave as-is until a node appears
    # Greedy: prefer the emptiest node. Stable tiebreak on node_id for determinism.
    ranked = sorted(nodes, key=lambda n: (-node_free_vram(n), n["node_id"]))
    target = ranked[0]["node_id"]
    changes: dict[str, str] = {}
    for key in desired:
        cur = assignments.get(key)
        if cur is None or cur not in alive:
            changes[key] = target
    return changes


class Scheduler:
    """Leader-only loop: keep desired instances placed on live nodes."""

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
        changes = place(desired, nodes, assignments)
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

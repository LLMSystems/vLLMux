"""Cross-node placement scheduler (HA Phase 3c).

Pure placement (greedy by free VRAM, dead-node failover, no churn) + the
reschedule pass against a store. Multi-node is unit-tested here since a second GPU
host can't run on one machine; on a single host placement is a no-op.
"""
import json

import pytest

from app.core.settings import BackendSettings
from app.llmops.scheduler import Scheduler, node_free_vram, place

pytestmark = pytest.mark.unit


def _node(node_id, *gpus):
    cap = json.dumps([{"memory_total": t, "memory_used": u} for t, u in gpus]) if gpus else None
    return {"node_id": node_id, "capacity": cap}


def test_node_free_vram_sums_gpus():
    assert node_free_vram(_node("n", (8000, 1000), (8000, 6000))) == 9000
    assert node_free_vram(_node("n")) == 0           # no capacity
    assert node_free_vram({"node_id": "n", "capacity": "bad json"}) == 0


def test_place_assigns_unassigned_to_emptiest_node():
    nodes = [_node("a", (8000, 7000)), _node("b", (8000, 1000))]  # b is emptier
    changes = place({"G::x"}, nodes, assignments={})
    assert changes == {"G::x": "b"}


def test_place_leaves_instances_already_on_a_live_node():
    nodes = [_node("a", (8000, 1000)), _node("b", (8000, 2000))]
    changes = place({"G::x"}, nodes, assignments={"G::x": "a"})
    assert changes == {}  # already placed on a live node -> no churn


def test_place_failover_reassigns_from_dead_node():
    # G::x was on 'gone' (not in the live node list) -> moved to a live node.
    nodes = [_node("b", (8000, 1000))]
    changes = place({"G::x"}, nodes, assignments={"G::x": "gone"})
    assert changes == {"G::x": "b"}


def test_place_no_nodes_keeps_pending():
    assert place({"G::x"}, nodes=[], assignments={}) == {}


async def test_reschedule_once_places_via_store():
    class _Store:
        def __init__(self):
            self.desired = {"G::x": "running", "G::y": "stopped"}
            self.nodes = [_node("a", (8000, 1000)), _node("b", (8000, 5000))]
            self.assignments = {}

        async def list_instance_desired(self):
            return dict(self.desired)

        async def list_nodes(self, ts=None):
            return list(self.nodes)

        async def list_assignments(self, node_id=None):
            return dict(self.assignments)

        async def set_assignment(self, key, node_id, ts=None):
            self.assignments[key] = node_id

    store = _Store()
    changes = await Scheduler().reschedule_once(store, BackendSettings())
    # Only the desired-running instance is placed, on the emptier node 'a'.
    assert changes == {"G::x": "a"}
    assert store.assignments == {"G::x": "a"}


async def test_reschedule_noop_without_store():
    assert await Scheduler().reschedule_once(None, BackendSettings()) == {}


# ---- engine-aware placement (HA Phase 7) ------------------------------------

from app.llmops.scheduler import node_supports  # noqa: E402


def _enode(node_id, engines, *gpus):
    n = _node(node_id, *gpus)
    n["engines"] = json.dumps(engines) if engines is not None else None
    return n


def test_node_supports_unspecified_runs_any():
    assert node_supports({"engines": None}, "sglang") is True
    assert node_supports({}, "vllm") is True


def test_node_supports_matches_advertised():
    n = _enode("n", ["sglang"])
    assert node_supports(n, "sglang") is True
    assert node_supports(n, "vllm") is False


def test_place_matches_engine_to_capable_node():
    nodes = [_enode("vllm-node", ["vllm"], (8192, 0)),
             _enode("sglang-node", ["sglang"], (8192, 0))]
    changes = place({"G::v", "G::s"}, nodes, {},
                    key_engines={"G::v": "vllm", "G::s": "sglang"})
    assert changes["G::v"] == "vllm-node"
    assert changes["G::s"] == "sglang-node"


def test_place_leaves_unassigned_when_no_capable_node():
    nodes = [_enode("vllm-node", ["vllm"], (8192, 0))]
    changes = place({"G::s"}, nodes, {}, key_engines={"G::s": "sglang"})
    assert "G::s" not in changes  # no sglang node -> stays unassigned


def test_place_moves_model_off_wrong_engine_node():
    # Started on the vllm node by mistake; scheduler moves it to the sglang node.
    nodes = [_enode("vllm-node", ["vllm"], (8192, 0)),
             _enode("sglang-node", ["sglang"], (8192, 0))]
    changes = place({"G::s"}, nodes, {"G::s": "vllm-node"}, key_engines={"G::s": "sglang"})
    assert changes["G::s"] == "sglang-node"


def test_place_keeps_well_placed_engine_match():
    nodes = [_enode("sglang-node", ["sglang"], (8192, 0))]
    changes = place({"G::s"}, nodes, {"G::s": "sglang-node"}, key_engines={"G::s": "sglang"})
    assert changes == {}  # already on a matching live node -> no churn

"""Node-agent heartbeat (HA Phase 3b-1).

The agent registers this node + its capacity into the shared store. Covered here:
heartbeat upserts the node, prunes lapsed peers, tolerates no-GPU / no-node-store.
"""
import pytest

from app.core.settings import BackendSettings
from app.llmops.node_agent import NodeAgent

pytestmark = pytest.mark.unit


class FakeNodeStore:
    def __init__(self):
        self.nodes = {}
        self.pruned = 0

    async def upsert_node(self, node_id, hostname, capacity, ttl, ts=None, engines=None,
                          api_url=None):
        self.nodes[node_id] = {"hostname": hostname, "capacity": capacity, "ttl": ttl,
                               "engines": engines, "api_url": api_url}

    async def prune_nodes(self, ts=None):
        self.pruned += 1
        return 0


def _settings():
    return BackendSettings(instance_id="node-A")


async def test_heartbeat_registers_node(monkeypatch):
    import app.llmops.node_agent as na
    monkeypatch.setattr(na, "get_gpu_info", lambda: [
        {"index": 0, "name": "RTX", "memory_total": 8192, "memory_used": 100},
    ])
    store = FakeNodeStore()
    agent = NodeAgent(store, _settings())
    await agent.heartbeat_once()

    assert "node-A" in store.nodes
    row = store.nodes["node-A"]
    assert row["ttl"] == _settings().node_ttl
    assert '"index": 0' in row["capacity"] and '"memory_total": 8192' in row["capacity"]
    assert store.pruned == 1  # housekeeping ran


async def test_heartbeat_without_gpus_registers_null_capacity(monkeypatch):
    import app.llmops.node_agent as na
    monkeypatch.setattr(na, "get_gpu_info", lambda: [])
    store = FakeNodeStore()
    agent = NodeAgent(store, _settings())
    await agent.heartbeat_once()
    assert store.nodes["node-A"]["capacity"] is None


async def test_heartbeat_noop_when_store_lacks_node_registry():
    class Bare:  # store without upsert_node (older SQLite)
        pass

    agent = NodeAgent(Bare(), _settings())
    await agent.heartbeat_once()  # must not raise


async def test_heartbeat_tolerates_gpu_probe_failure(monkeypatch):
    import app.llmops.node_agent as na

    def boom():
        raise RuntimeError("nvidia-smi exploded")

    monkeypatch.setattr(na, "get_gpu_info", boom)
    store = FakeNodeStore()
    agent = NodeAgent(store, _settings())
    await agent.heartbeat_once()
    assert store.nodes["node-A"]["capacity"] is None


async def test_heartbeat_advertises_engines(monkeypatch):
    import app.llmops.node_agent as na
    monkeypatch.setattr(na, "get_gpu_info", lambda: [])
    store = FakeNodeStore()
    agent = NodeAgent(store, BackendSettings(instance_id="node-A", node_engines=["sglang"]))
    await agent.heartbeat_once()
    assert store.nodes["node-A"]["engines"] == '["sglang"]'


async def test_heartbeat_engines_null_when_unspecified(monkeypatch):
    import app.llmops.node_agent as na
    monkeypatch.setattr(na, "get_gpu_info", lambda: [])
    store = FakeNodeStore()
    agent = NodeAgent(store, BackendSettings(instance_id="node-A"))  # no node_engines
    await agent.heartbeat_once()
    assert store.nodes["node-A"]["engines"] is None

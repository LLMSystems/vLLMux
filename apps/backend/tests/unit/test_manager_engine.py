"""Multi-engine dispatch: the manager picks a launcher by (kind, engine), and the
engine threads from config -> registry -> instance. See
docs/multi-backend-engine-design_zh-CN.md."""
import pytest

from app.core.settings import BackendSettings
from app.llmops.instance import LaunchSpec
from app.llmops.launchers import (CAP_RUNTIME_LORA, CAP_SLEEP, EmbeddingLauncher,
                                  VllmLauncher)
from app.llmops.manager import ModelConflict, ModelManager, build_registry
from app.llmops.state import ModelKind
from schema import load_config

pytestmark = pytest.mark.unit


class _FakeLauncher:
    """A minimal second LLM engine with no optional capabilities — stands in for a
    future engine to exercise capability gating + dispatch. Uses the "sglang"
    engine slot (a valid schema Literal) but declares no capabilities of its own."""
    kind = ModelKind.LLM
    engine = "sglang"
    capabilities = frozenset()

    def keys(self, config):
        out = []
        for tag, eng in config.LLM_engines.items():
            if getattr(eng.settings, "engine", "vllm") != self.engine:
                continue
            out.extend(f"{tag}::{i.id}" for i in eng.instances)
        return out

    def build_spec(self, config, config_path, key):
        tag, _, iid = key.partition("::")
        inst = next(i for i in config.LLM_engines[tag].instances if i.id == iid)
        return LaunchSpec(
            key=key, kind=self.kind, engine=self.engine, capabilities=self.capabilities,
            command=["fake-serve"], env={}, log_path="x.log",
            host=inst.host, port=inst.port, probe_url=f"http://{inst.host}:{inst.port}/health",
            model_tag=config.LLM_engines[tag].settings.model_tag,
        )

# Two groups: one default (vllm), one explicitly configured for a not-yet-built
# engine (sglang). Only VllmLauncher is registered, so the sglang group is simply
# not claimed — proving keys() filters by engine rather than erroring.
CONFIG_YAML = """
server:
  port: 8887
LLM_engines:
  Qwen3-0.6B:
    instances:
      - id: a
        host: localhost
        port: 8002
    model_config:
      model_tag: Qwen/Qwen3-0.6B
  FutureModel:
    instances:
      - id: a
        host: localhost
        port: 8010
    model_config:
      model_tag: org/future
      engine: sglang
"""


def _manager(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(str(cfg_path))
    launchers = [VllmLauncher(), EmbeddingLauncher()]
    registry = build_registry(config, str(cfg_path), launchers)
    mgr = ModelManager(
        registry, launchers, None, config, str(cfg_path),
        BackendSettings(), store=None, overlay_path=str(tmp_path / "overlay.json"),
    )
    return mgr


def test_build_registry_threads_engine_and_filters_unclaimed(tmp_path):
    mgr = _manager(tmp_path)
    # vLLM group is registered with engine="vllm" on the instance.
    vllm_inst = mgr.registry.get("Qwen3-0.6B::a")
    assert vllm_inst is not None
    assert vllm_inst.engine == "vllm"
    assert vllm_inst.spec.engine == "vllm"
    # The sglang group has no launcher registered for it -> not in the registry.
    assert mgr.registry.get("FutureModel::a") is None


def test_launcher_for_dispatches_by_kind_and_engine(tmp_path):
    mgr = _manager(tmp_path)
    inst = mgr.registry.get("Qwen3-0.6B::a")
    launcher = mgr._launcher_for(inst)
    assert isinstance(launcher, VllmLauncher)
    assert (launcher.kind, launcher.engine) == (ModelKind.LLM, "vllm")


def test_observed_dict_includes_engine(tmp_path):
    mgr = _manager(tmp_path)
    inst = mgr.registry.get("Qwen3-0.6B::a")
    assert inst.observed_dict()["engine"] == "vllm"


async def test_create_overlay_model_records_engine(tmp_path):
    """A dashboard-added model (default engine) registers with engine='vllm'."""
    mgr = _manager(tmp_path)
    inst = await mgr.create_overlay_model(
        "NewGroup",
        {"id": "x", "host": "localhost", "port": 8020},
        {"model_tag": "org/new"},
    )
    assert inst.engine == "vllm"
    assert mgr.registry.get("NewGroup::x").engine == "vllm"


# ---- capability gating ------------------------------------------------------

FAKE_ENGINE_YAML = """
server:
  port: 8887
LLM_engines:
  Plain:
    instances:
      - id: a
        host: localhost
        port: 8030
    model_config:
      model_tag: org/plain
      engine: sglang
      allow_runtime_lora: true
"""


def _manager_with_fake(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(FAKE_ENGINE_YAML, encoding="utf-8")
    config = load_config(str(cfg_path))
    launchers = [VllmLauncher(), _FakeLauncher(), EmbeddingLauncher()]
    registry = build_registry(config, str(cfg_path), launchers)
    return ModelManager(
        registry, launchers, None, config, str(cfg_path),
        BackendSettings(), store=None, overlay_path=str(tmp_path / "overlay.json"),
    )


def test_capabilities_resolved_per_engine(tmp_path):
    mgr = _manager_with_fake(tmp_path)
    # fake engine declares no capabilities; vllm would declare sleep + lora.
    assert mgr._llm_engine_capabilities("Plain") == frozenset()
    assert CAP_SLEEP not in mgr._llm_engine_capabilities("Plain")


def test_fake_engine_group_is_dispatched_to_its_launcher(tmp_path):
    mgr = _manager_with_fake(tmp_path)
    inst = mgr.registry.get("Plain::a")
    assert inst is not None and inst.engine == "sglang"
    assert isinstance(mgr._launcher_for(inst), _FakeLauncher)


async def test_load_lora_rejected_when_engine_lacks_capability(tmp_path):
    """Even with allow_runtime_lora set in config, an engine without the
    runtime_lora capability must be refused (gate on capability, not the flag)."""
    mgr = _manager_with_fake(tmp_path)
    with pytest.raises(ModelConflict, match="runtime LoRA"):
        await mgr.load_lora("Plain", "adapter", "repo/adapter")


async def test_create_overlay_model_rejects_unregistered_engine(tmp_path):
    # trtllm is a valid engine name in the schema, but no launcher is registered
    # for it here — the manager must refuse cleanly, not KeyError into a 500.
    mgr = _manager_with_fake(tmp_path)
    with pytest.raises(ModelConflict, match="unsupported engine"):
        await mgr.create_overlay_model(
            "Brand", {"id": "z", "host": "localhost", "port": 8040},
            {"model_tag": "org/brand", "engine": "trtllm"},
        )


# ---- per-engine runtime LoRA endpoint path ----------------------------------

def _inst(engine: str, port: int):
    from app.llmops.instance import LaunchSpec, ModelInstance
    spec = LaunchSpec(key=f"G::a", kind=ModelKind.LLM, engine=engine, capabilities=frozenset(),
                      command=[], env={}, log_path="x", host="localhost", port=port,
                      probe_url=f"http://localhost:{port}/health")
    return ModelInstance(key="G::a", kind=ModelKind.LLM, engine=engine, host="localhost",
                         port=port, spec=spec)


async def test_post_lora_endpoint_path_per_engine(tmp_path):
    from tests.conftest import FakeHTTPClient
    client = FakeHTTPClient()
    mgr = ModelManager(build_registry(load_config(_write_min_cfg(tmp_path)), str(tmp_path/'c.yaml'),
                                      [VllmLauncher()]),
                       [VllmLauncher()], client, load_config(_write_min_cfg(tmp_path)),
                       str(tmp_path/'c.yaml'), BackendSettings(), store=None,
                       overlay_path=str(tmp_path/'o.json'))
    await mgr._post_lora(_inst("vllm", 8002), "load", {"lora_name": "x", "lora_path": "/p"})
    await mgr._post_lora(_inst("sglang", 8100), "load", {"lora_name": "x", "lora_path": "/p"})
    urls = [u for u, _ in client.posts]
    assert urls[0] == "http://localhost:8002/v1/load_lora_adapter"   # vLLM: /v1 prefix
    assert urls[1] == "http://localhost:8100/load_lora_adapter"      # SGLang: no /v1


def _write_min_cfg(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("server:\n  port: 8887\nLLM_engines: {}\n", encoding="utf-8")
    return str(p)


# ---- HA Phase 7C: API write-intent when this node can't run the engine -------

from app.llmops.launchers import SglangLauncher  # noqa: E402
from app.llmops.state import Desired, ModelState  # noqa: E402


class _DesiredRecordingStore:
    def __init__(self):
        self.desired: dict[str, str] = {}
        self.deleted_assignments: list[str] = []

    async def set_instance_desired(self, key, val): self.desired[key] = val
    async def delete_assignment(self, key): self.deleted_assignments.append(key)


SGLANG_ONLY_YAML = """
server:
  port: 8887
LLM_engines:
  S:
    instances: [{ id: a, host: localhost, port: 8100 }]
    model_config: { model_tag: org/s, engine: sglang }
"""


def _mgr_node_engines(tmp_path, node_engines):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SGLANG_ONLY_YAML, encoding="utf-8")
    config = load_config(str(cfg))
    launchers = [VllmLauncher(), SglangLauncher(), EmbeddingLauncher()]
    registry = build_registry(config, str(cfg), launchers)
    store = _DesiredRecordingStore()
    mgr = ModelManager(registry, launchers, None, config, str(cfg),
                       BackendSettings(node_engines=node_engines), store=store,
                       overlay_path=str(tmp_path / "o.json"))
    return mgr, store


async def test_start_defers_when_node_cannot_run_engine(tmp_path):
    # A vLLM-only node asked to start a SGLang model: write intent, do NOT spawn.
    mgr, store = _mgr_node_engines(tmp_path, ["vllm"])
    inst = await mgr.start("S::a")
    assert inst.state == ModelState.STOPPED        # never spawned locally
    assert inst.desired == Desired.RUNNING         # intent recorded
    assert store.desired["S::a"] == "running"
    assert "S::a" in store.deleted_assignments     # cleared so scheduler re-places


async def test_node_can_run_all_when_unspecified(tmp_path):
    # No node_engines = runs any engine -> _node_can_run True (collapsed unchanged).
    mgr, _ = _mgr_node_engines(tmp_path, [])
    assert mgr._node_can_run("sglang") is True
    assert mgr._node_can_run("vllm") is True


async def test_node_can_run_respects_advertised(tmp_path):
    mgr, _ = _mgr_node_engines(tmp_path, ["vllm"])
    assert mgr._node_can_run("vllm") is True
    assert mgr._node_can_run("sglang") is False


# ---- HA Phase 7: owning-node API URL (for log/metric proxy) ------------------

class _AssignNodeStore:
    db_url = "postgresql://x"  # marks HA mode
    def __init__(self, assignments, nodes):
        self._a = assignments
        self._n = nodes
    async def list_assignments(self): return dict(self._a)
    async def list_nodes(self): return list(self._n)
    async def list_instance_observed(self): return []


async def test_owning_node_api_url_for_foreign_model(tmp_path):
    store = _AssignNodeStore(
        {"S::a": "sglang-node"},
        [{"node_id": "sglang-node", "api_url": "http://sglang-backend:5000"}],
    )
    cfg = tmp_path / "c.yaml"; cfg.write_text("server:\n  port: 8887\nLLM_engines: {}\n")
    mgr = ModelManager(build_registry(load_config(str(cfg)), str(cfg), [VllmLauncher()]),
                       [VllmLauncher()], None, load_config(str(cfg)), str(cfg),
                       BackendSettings(instance_id="vllm-node"), store=store,
                       overlay_path=str(tmp_path/"o.json"))
    assert await mgr.owning_node_api_url("S::a") == "http://sglang-backend:5000"


async def test_owning_node_api_url_none_when_local(tmp_path):
    store = _AssignNodeStore(
        {"S::a": "vllm-node"},  # owned by us
        [{"node_id": "vllm-node", "api_url": "http://vllm-backend:5000"}],
    )
    cfg = tmp_path / "c.yaml"; cfg.write_text("server:\n  port: 8887\nLLM_engines: {}\n")
    mgr = ModelManager(build_registry(load_config(str(cfg)), str(cfg), [VllmLauncher()]),
                       [VllmLauncher()], None, load_config(str(cfg)), str(cfg),
                       BackendSettings(instance_id="vllm-node"), store=store,
                       overlay_path=str(tmp_path/"o.json"))
    assert await mgr.owning_node_api_url("S::a") is None  # local -> read locally

"""Config export / import (overlay backup & restore) + registry resync.

Covers manager.import_overlay's validate -> running-guard -> save -> resync ->
reload funnel and resync_registry's add/remove/changed accounting. The store-side
snapshot table is covered in packages/llmops-store/tests; the auto-snapshot
middleware + version routes are covered in tests/api/test_config_routes.py.
"""
import pytest

from app.core.settings import BackendSettings
from app.llmops.launchers import EmbeddingLauncher, VllmLauncher
from app.llmops.manager import (
    ConfigInvalid,
    ModelConflict,
    ModelManager,
    build_registry,
)
from app.llmops.state import ModelState
from app.services.overlay import load_overlay
from schema import load_config

pytestmark = pytest.mark.unit

CONFIG_YAML = """
server:
  port: 8887
LLM_engines:
  Qwen3-0.6B:
    instances:
      - id: a
        host: localhost
        port: 8002
        cuda_device: 0
    model_config:
      model_tag: Qwen/Qwen3-0.6B
      max_model_len: 500
      gpu_memory_utilization: 0.35
"""


class FakeHTTP:
    def __init__(self):
        self.posts = []

    async def post(self, url, *a, **k):
        self.posts.append(url)
        return type("R", (), {"status_code": 200})()


def _manager(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG_YAML, encoding="utf-8")
    overlay_path = tmp_path / "overlay.json"
    config = load_config(str(cfg_path))
    launchers = [VllmLauncher(), EmbeddingLauncher()]
    registry = build_registry(config, str(cfg_path), launchers)
    mgr = ModelManager(
        registry, launchers, FakeHTTP(), config, str(cfg_path),
        BackendSettings(), store=None, overlay_path=str(overlay_path),
        router_url="http://router",
    )
    return mgr, overlay_path


def _overlay_with_extra_group():
    return {"LLM_engines": {"Extra": {
        "instances": [{"id": "x", "host": "localhost", "port": 9300, "cuda_device": 0}],
        "model_config": {"model_tag": "some/Model", "max_model_len": 256,
                         "gpu_memory_utilization": 0.4},
    }}}


async def test_export_round_trips_overlay(tmp_path):
    mgr, _ = _manager(tmp_path)
    await mgr.create_overlay_model(
        "Qwen3-0.6B", {"id": "b", "host": "localhost", "port": 8004, "cuda_device": 0},
        {"model_tag": "Qwen/Qwen3-0.6B"},
    )
    exported = mgr.export_overlay()
    assert exported["LLM_engines"]["Qwen3-0.6B"]["instances"][0]["id"] == "b"


async def test_import_adds_and_registers_new_group(tmp_path):
    mgr, overlay_path = _manager(tmp_path)
    summary = await mgr.import_overlay(_overlay_with_extra_group())
    assert summary["added"] == ["Extra::x"]
    assert summary["removed"] == [] and summary["changed"] == []
    assert mgr.registry.get("Extra::x") is not None
    assert mgr.registry.get("Extra::x").state == ModelState.STOPPED
    assert load_overlay(str(overlay_path))["LLM_engines"]["Extra"]  # persisted
    assert mgr.http_client.posts  # router reload nudged


async def test_import_removes_overlay_only_instances(tmp_path):
    mgr, _ = _manager(tmp_path)
    await mgr.import_overlay(_overlay_with_extra_group())
    assert mgr.registry.get("Extra::x") is not None
    # Re-import an empty overlay: the overlay-only group disappears, the
    # config.yaml-defined one stays.
    summary = await mgr.import_overlay({"LLM_engines": {}})
    assert summary["removed"] == ["Extra::x"]
    assert mgr.registry.get("Extra::x") is None
    assert mgr.registry.get("Qwen3-0.6B::a") is not None


async def test_import_detects_changed_spec(tmp_path):
    mgr, _ = _manager(tmp_path)
    # Override the config.yaml instance's port via the overlay.
    overlay = {"LLM_engines": {"Qwen3-0.6B": {
        "instances": [{"id": "a", "host": "localhost", "port": 9999, "cuda_device": 0}],
    }}}
    summary = await mgr.import_overlay(overlay)
    assert summary["changed"] == ["Qwen3-0.6B::a"]
    assert mgr.registry.get("Qwen3-0.6B::a").port == 9999


async def test_import_rejects_invalid_overlay(tmp_path):
    mgr, _ = _manager(tmp_path)
    with pytest.raises(ConfigInvalid):
        # model_config without a model_tag fails schema validation for a new group.
        await mgr.import_overlay({"LLM_engines": {"Bad": {"instances": [{"id": "z"}]}}})


async def test_import_refuses_when_affected_instance_running(tmp_path):
    mgr, _ = _manager(tmp_path)
    mgr.registry.get("Qwen3-0.6B::a").state = ModelState.READY
    overlay = {"LLM_engines": {"Qwen3-0.6B": {
        "instances": [{"id": "a", "host": "localhost", "port": 9999, "cuda_device": 0}],
    }}}
    with pytest.raises(ModelConflict):
        await mgr.import_overlay(overlay)
    # Unchanged: still on the original port.
    assert mgr.registry.get("Qwen3-0.6B::a").port == 8002


async def test_import_force_stops_running_then_applies(tmp_path):
    mgr, _ = _manager(tmp_path)
    inst = mgr.registry.get("Qwen3-0.6B::a")
    inst.state = ModelState.READY
    overlay = {"LLM_engines": {"Qwen3-0.6B": {
        "instances": [{"id": "a", "host": "localhost", "port": 9999, "cuda_device": 0}],
    }}}
    summary = await mgr.import_overlay(overlay, force=True)
    assert summary["changed"] == ["Qwen3-0.6B::a"]
    assert mgr.registry.get("Qwen3-0.6B::a").port == 9999

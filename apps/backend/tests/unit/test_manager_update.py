"""update_overlay_model: editing a config.yaml model persists an overlay
override, re-resolves the spec, and refuses while the model is running."""
import pytest

from app.core.settings import BackendSettings
from app.llmops.launchers import EmbeddingLauncher, VllmLauncher
from app.llmops.manager import ModelConflict, ModelManager, build_registry
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


def _manager(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG_YAML, encoding="utf-8")
    overlay_path = tmp_path / "overlay.json"
    config = load_config(str(cfg_path))
    launchers = [VllmLauncher(), EmbeddingLauncher()]
    registry = build_registry(config, str(cfg_path), launchers)
    mgr = ModelManager(
        registry, launchers, None, config, str(cfg_path),
        BackendSettings(), store=None, overlay_path=str(overlay_path),
    )
    return mgr, overlay_path


async def test_edit_persists_overlay_override(tmp_path):
    mgr, overlay_path = _manager(tmp_path)
    inst = await mgr.update_overlay_model(
        "Qwen3-0.6B::a",
        {"id": "a", "host": "localhost", "port": 9100, "cuda_device": 1},
        {"model_tag": "Qwen/Qwen3-0.6B", "max_model_len": 500, "gpu_memory_utilization": 0.8},
    )
    # registry instance re-resolved to the new port
    assert inst.port == 9100
    # overlay file written with the override
    overlay = load_overlay(str(overlay_path))
    entry = overlay["LLM_engines"]["Qwen3-0.6B"]
    assert entry["instances"][0]["port"] == 9100
    assert entry["model_config"]["gpu_memory_utilization"] == 0.8
    # live merged config reflects the edit
    merged = mgr.config.LLM_engines["Qwen3-0.6B"]
    assert merged.settings.gpu_memory_utilization == 0.8


async def test_edit_ignores_attempted_id_change(tmp_path):
    mgr, _ = _manager(tmp_path)
    inst = await mgr.update_overlay_model(
        "Qwen3-0.6B::a",
        {"id": "hacked", "host": "localhost", "port": 8002, "cuda_device": 0},
        {"model_tag": "Qwen/Qwen3-0.6B"},
    )
    assert inst.key == "Qwen3-0.6B::a"


async def test_edit_rejected_while_running(tmp_path):
    mgr, _ = _manager(tmp_path)
    mgr.registry.get("Qwen3-0.6B::a").state = ModelState.READY
    with pytest.raises(ModelConflict):
        await mgr.update_overlay_model(
            "Qwen3-0.6B::a",
            {"id": "a", "port": 8002},
            {"model_tag": "Qwen/Qwen3-0.6B"},
        )


async def test_edit_rejects_port_collision(tmp_path):
    mgr, _ = _manager(tmp_path)
    # add a second instance so 8004 is taken, then try to move 'a' onto it
    await mgr.update_overlay_model(
        "Qwen3-0.6B::a", {"id": "a", "port": 8002}, {"model_tag": "Qwen/Qwen3-0.6B"}
    )
    await mgr.create_overlay_model(
        "Qwen3-0.6B", {"id": "b", "host": "localhost", "port": 8004, "cuda_device": 0},
        {"model_tag": "Qwen/Qwen3-0.6B"},
    )
    with pytest.raises(ModelConflict):
        await mgr.update_overlay_model(
            "Qwen3-0.6B::a", {"id": "a", "port": 8004}, {"model_tag": "Qwen/Qwen3-0.6B"}
        )

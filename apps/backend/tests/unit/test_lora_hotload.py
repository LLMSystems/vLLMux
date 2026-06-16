"""Runtime (hot) LoRA load/unload on the ModelManager: fan-out + rollback +
overlay persistence."""
import json
from types import SimpleNamespace

import pytest
import yaml

from app.llmops.manager import LoraRuntimeError, ModelConflict, ModelManager, ModelNotFound
from app.llmops.registry import ModelRegistry
from app.llmops.state import ModelKind, ModelState
from app.core.settings import BackendSettings
from schema import RootConfig

pytestmark = pytest.mark.unit


class _Resp:
    def __init__(self, code):
        self.status_code = code
        self.text = "boom" if code >= 400 else "ok"


class FakeClient:
    """Records load/unload posts; instances on `fail_ports` return 500."""

    def __init__(self, fail_ports=()):
        self.calls: list[tuple[str, dict]] = []
        self.fail_ports = set(fail_ports)

    async def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        port = int(url.split(":")[2].split("/")[0])
        return _Resp(500 if port in self.fail_ports else 200)


def _config(allow_runtime=True) -> RootConfig:
    return RootConfig.model_validate({
        "server": {"host": "0.0.0.0", "port": 8887},
        "LLM_engines": {
            "Qwen": {
                "instances": [
                    {"id": "a", "host": "localhost", "port": 8002, "cuda_device": 0},
                    {"id": "b", "host": "localhost", "port": 8004, "cuda_device": 0},
                ],
                "model_config": {
                    "model_tag": "Qwen/Qwen3-0.6B",
                    "enable_lora": True,
                    "allow_runtime_lora": allow_runtime,
                },
            }
        },
    })


def _ready_instances(ports):
    reg = ModelRegistry()
    for i, p in enumerate(ports):
        reg.add(SimpleNamespace(
            key=f"Qwen::{chr(97 + i)}", kind=ModelKind.LLM, host="localhost",
            port=p, state=ModelState.READY,
        ))
    return reg


def _mgr(config, client, registry):
    return ModelManager(registry, [], client, config, "config.yaml",
                        BackendSettings(), overlay_path="overlay.json")


async def test_load_fans_out_to_all_ready_instances(monkeypatch):
    client = FakeClient()
    mgr = _mgr(_config(), client, _ready_instances([8002, 8004]))
    persisted = {}
    monkeypatch.setattr(mgr, "_persist_lora", lambda *a, **k: persisted.update(args=a, kw=k))

    res = await mgr.load_lora("Qwen", "sql", "/lora/sql", "Qwen/Qwen3-0.6B")

    loads = [c for c in client.calls if c[0].endswith("/load_lora_adapter")]
    assert {c[0] for c in loads} == {
        "http://localhost:8002/v1/load_lora_adapter",
        "http://localhost:8004/v1/load_lora_adapter",
    }
    assert all(c[1] == {"lora_name": "sql", "lora_path": "/lora/sql"} for c in loads)
    assert res["instances"] == ["Qwen::a", "Qwen::b"]
    assert persisted["kw"]["remove"] is False  # persisted only after full success


@pytest.mark.asyncio
async def test_partial_failure_rolls_back_and_does_not_persist(monkeypatch):
    client = FakeClient(fail_ports={8004})  # 2nd instance fails
    mgr = _mgr(_config(), client, _ready_instances([8002, 8004]))
    monkeypatch.setattr(mgr, "_persist_lora", lambda *a, **k: pytest.fail("must not persist"))

    with pytest.raises(LoraRuntimeError):
        await mgr.load_lora("Qwen", "sql", "/lora/sql")

    # the first instance loaded, then got rolled back (unload) to stay consistent.
    assert ("http://localhost:8002/v1/load_lora_adapter", {"lora_name": "sql", "lora_path": "/lora/sql"}) in client.calls
    assert ("http://localhost:8002/v1/unload_lora_adapter", {"lora_name": "sql"}) in client.calls


@pytest.mark.asyncio
async def test_load_rejected_when_runtime_lora_disabled():
    mgr = _mgr(_config(allow_runtime=False), FakeClient(), _ready_instances([8002]))
    with pytest.raises(ModelConflict):
        await mgr.load_lora("Qwen", "sql", "/lora/sql")


@pytest.mark.asyncio
async def test_load_rejected_when_no_ready_instance():
    mgr = _mgr(_config(), FakeClient(), ModelRegistry())  # empty registry
    with pytest.raises(ModelConflict):
        await mgr.load_lora("Qwen", "sql", "/lora/sql")


@pytest.mark.asyncio
async def test_load_unknown_group():
    mgr = _mgr(_config(), FakeClient(), _ready_instances([8002]))
    with pytest.raises(ModelNotFound):
        await mgr.load_lora("Ghost", "sql", "/lora/sql")


@pytest.mark.asyncio
async def test_persist_writes_lora_into_overlay(tmp_path):
    # Real config.yaml + overlay so _persist_lora's build_merged_config round-trips.
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "server": {"host": "0.0.0.0", "port": 8887},
        "LLM_engines": {
            "Qwen": {
                "instances": [{"id": "a", "host": "localhost", "port": 8002, "cuda_device": 0}],
                "model_config": {"model_tag": "Qwen/Qwen3-0.6B", "enable_lora": True,
                                 "allow_runtime_lora": True},
            }
        },
    }))
    overlay_path = tmp_path / "overlay.json"
    from schema import load_config
    mgr = ModelManager(ModelRegistry(), [], FakeClient(), load_config(str(cfg_path)),
                       str(cfg_path), BackendSettings(), overlay_path=str(overlay_path))

    mgr._persist_lora("Qwen", "sql", "/lora/sql", "Qwen/Qwen3-0.6B", remove=False)
    saved = json.loads(overlay_path.read_text())
    loras = saved["LLM_engines"]["Qwen"]["model_config"]["lora_modules"]
    assert loras == [{"name": "sql", "path": "/lora/sql", "base_model_name": "Qwen/Qwen3-0.6B"}]
    # self.config reflects it too.
    assert mgr.config.LLM_engines["Qwen"].settings.lora_modules[0].name == "sql"

    # Removing drops it.
    mgr._persist_lora("Qwen", "sql", None, None, remove=True)
    saved = json.loads(overlay_path.read_text())
    assert saved["LLM_engines"]["Qwen"]["model_config"]["lora_modules"] == []

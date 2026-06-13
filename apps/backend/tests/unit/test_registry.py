import pytest

from app.llmops.launchers import EmbeddingLauncher, VllmLauncher
from app.llmops.manager import build_registry
from app.llmops.state import ModelState
from tests.conftest import FAKE_CONFIG

pytestmark = pytest.mark.unit


def test_build_registry_creates_one_record_per_instance_all_stopped():
    reg = build_registry(FAKE_CONFIG, "config.yaml", [VllmLauncher(), EmbeddingLauncher()])
    assert set(reg.keys()) == {
        "Qwen3-0.6B::qwen3",
        "Qwen3-0.6B::qwen3-2",
        "embedding::default",
    }
    assert all(i.state == ModelState.STOPPED for i in reg.values())


def test_get_returns_none_for_unknown_key():
    reg = build_registry(FAKE_CONFIG, "config.yaml", [VllmLauncher()])
    assert reg.get("does-not-exist") is None


async def test_snapshot_returns_all_instances():
    reg = build_registry(FAKE_CONFIG, "config.yaml", [VllmLauncher()])
    snap = await reg.snapshot()
    assert {i.key for i in snap} == {"Qwen3-0.6B::qwen3", "Qwen3-0.6B::qwen3-2"}

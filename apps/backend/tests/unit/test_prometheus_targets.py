import json

import pytest

from app.core.settings import BackendSettings
from app.llmops.launchers import EMBEDDING_KEY, EmbeddingLauncher, VllmLauncher
from app.llmops.manager import ModelManager, build_registry
from app.llmops.state import ModelState
from app.services.prometheus_targets import (build_targets, render,
                                             write_targets_file)
from tests.conftest import FAKE_CONFIG, FakeHTTPClient

pytestmark = pytest.mark.unit

HEALTHY = "Qwen3-0.6B::qwen3"       # port 8002
OTHER = "Qwen3-0.6B::qwen3-2"       # port 8004


def _registry():
    return build_registry(FAKE_CONFIG, "config.yaml", [VllmLauncher(), EmbeddingLauncher()])


def test_build_targets_only_includes_ready_llm():
    reg = _registry()
    reg.get(HEALTHY).state = ModelState.READY
    reg.get(OTHER).state = ModelState.STARTING  # not ready -> excluded

    targets = build_targets(reg.values())

    assert len(targets) == 1
    entry = targets[0]
    assert entry["targets"] == ["localhost:8002"]
    assert entry["labels"]["group"] == "Qwen3-0.6B"
    assert entry["labels"]["instance_id"] == "qwen3"
    assert entry["labels"]["model_tag"] == "Qwen/Qwen3-0.6B"


def test_build_targets_excludes_embedding_server():
    # The embedding/reranker server is not vLLM and exposes no Prometheus metrics.
    reg = _registry()
    emb = reg.get(EMBEDDING_KEY)
    assert emb is not None
    emb.state = ModelState.READY

    assert build_targets(reg.values()) == []


def test_build_targets_is_sorted_and_stable():
    reg = _registry()
    reg.get(HEALTHY).state = ModelState.READY   # 8002
    reg.get(OTHER).state = ModelState.READY     # 8004

    addrs = [t["targets"][0] for t in build_targets(reg.values())]
    assert addrs == ["localhost:8002", "localhost:8004"]  # sorted by address


def test_write_targets_file_writes_then_skips_unchanged(tmp_path):
    path = str(tmp_path / "sub" / "targets.json")  # parent created on demand
    targets = [{"targets": ["localhost:8002"], "labels": {"group": "g"}}]

    assert write_targets_file(path, targets) is True   # first write
    assert json.loads(open(path).read()) == targets
    assert write_targets_file(path, targets) is False  # identical -> skip

    targets2 = targets + [{"targets": ["localhost:8004"], "labels": {"group": "g"}}]
    assert write_targets_file(path, targets2) is True  # changed -> rewrite
    assert json.loads(open(path).read()) == targets2


def test_write_targets_file_leaves_no_tmp_artifact(tmp_path):
    path = tmp_path / "targets.json"
    write_targets_file(str(path), [])
    assert not (tmp_path / "targets.json.tmp").exists()
    assert path.read_text() == render([])


async def test_manager_noop_without_path_configured():
    # Default settings leave prometheus_sd_path empty -> feature disabled.
    reg = _registry()
    mgr = ModelManager(
        reg, [VllmLauncher(), EmbeddingLauncher()], FakeHTTPClient(),
        FAKE_CONFIG, "config.yaml", BackendSettings(),
    )
    assert await mgr.write_prometheus_targets() is False


async def test_manager_writes_ready_targets_when_path_set(tmp_path):
    path = str(tmp_path / "targets.json")
    reg = _registry()
    reg.get(HEALTHY).state = ModelState.READY
    settings = BackendSettings(prometheus_sd_path=path)
    mgr = ModelManager(
        reg, [VllmLauncher(), EmbeddingLauncher()], FakeHTTPClient(),
        FAKE_CONFIG, "config.yaml", settings,
    )

    assert await mgr.write_prometheus_targets() is True
    written = json.loads(open(path).read())
    assert [t["targets"][0] for t in written] == ["localhost:8002"]

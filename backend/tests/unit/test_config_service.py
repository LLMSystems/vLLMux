import pytest

from app.services.config_service import summarize_config
from tests.conftest import FAKE_CONFIG

pytestmark = pytest.mark.unit


def test_flattens_instances_into_group_instance_keys():
    summary = summarize_config(FAKE_CONFIG)
    assert set(summary["LLM_engines"]) == {"Qwen3-0.6B::qwen3", "Qwen3-0.6B::qwen3-2"}


def test_instance_inherits_shared_model_config():
    summary = summarize_config(FAKE_CONFIG)
    entry = summary["LLM_engines"]["Qwen3-0.6B::qwen3"]
    assert entry["port"] == 8002
    assert entry["cuda_device"] == 0
    assert entry["max_model_len"] == 500  # from model_config
    assert entry["gpu_memory_utilization"] == 0.35
    assert entry["tool_parser"] == "unknown"  # nothing configured -> fallback


def test_embedding_summary_lists_model_names():
    summary = summarize_config(FAKE_CONFIG)
    emb = summary["embedding_server"]
    assert emb["port"] == 8005
    assert sorted(emb["embedding_models"]) == ["bge-m3", "m3e-base"]
    assert emb["reranking_models"] == ["bge-reranker-large"]


def test_instances_without_id_are_skipped():
    cfg = {"LLM_engines": {"M": {"instances": [{"port": 9}], "model_config": {}}}}
    assert summarize_config(cfg)["LLM_engines"] == {}


def test_missing_sections_yield_empty_summary():
    summary = summarize_config({})
    assert summary["LLM_engines"] == {}
    assert summary["embedding_server"]["embedding_models"] == []
    assert summary["embedding_server"]["reranking_models"] == []
    assert summary["server"] == {}

"""Overlay merge semantics: a new group is added whole; an existing (config.yaml)
group is overridden by the overlay so the UI can edit config models in place."""
import pytest

from app.services.overlay import config_owns, merge_into

pytestmark = pytest.mark.unit


BASE = {
    "server": {"port": 8887},
    "LLM_engines": {
        "Qwen3-0.6B": {
            "instances": [
                {"id": "a", "host": "localhost", "port": 8002, "cuda_device": 0},
                {"id": "b", "host": "localhost", "port": 8004, "cuda_device": 0},
            ],
            "model_config": {
                "model_tag": "Qwen/Qwen3-0.6B",
                "max_model_len": 500,
                "gpu_memory_utilization": 0.35,
            },
        }
    },
}


def test_new_group_is_added_whole():
    overlay = {"LLM_engines": {"NewModel": {"instances": [{"id": "x", "port": 9000}],
                                            "model_config": {"model_tag": "org/new"}}}}
    merged = merge_into(BASE, overlay)
    assert "NewModel" in merged["LLM_engines"]
    assert merged["LLM_engines"]["NewModel"]["instances"][0]["port"] == 9000


def test_overlay_sets_autoscale_on_existing_group():
    overlay = {"LLM_engines": {"Qwen3-0.6B": {"autoscale": {"enabled": True, "min_ready": 2}}}}
    merged = merge_into(BASE, overlay)
    grp = merged["LLM_engines"]["Qwen3-0.6B"]
    assert grp["autoscale"] == {"enabled": True, "min_ready": 2}
    # instances + model_config from the base survive an autoscale-only overlay.
    assert len(grp["instances"]) == 2 and grp["model_config"]["model_tag"] == "Qwen/Qwen3-0.6B"


def test_overlay_autoscale_replaces_base_policy():
    base = {"LLM_engines": {"Q": {"instances": [{"id": "a", "port": 1}],
                                  "model_config": {"model_tag": "o/q"},
                                  "autoscale": {"enabled": True, "min_ready": 3}}}}
    overlay = {"LLM_engines": {"Q": {"autoscale": {"enabled": False}}}}
    merged = merge_into(base, overlay)
    assert merged["LLM_engines"]["Q"]["autoscale"] == {"enabled": False}


def test_overlay_sets_and_replaces_fallback_chain():
    merged = merge_into(BASE, {"LLM_engines": {"Qwen3-0.6B": {"fallback": ["Other"]}}})
    grp = merged["LLM_engines"]["Qwen3-0.6B"]
    assert grp["fallback"] == ["Other"]
    assert len(grp["instances"]) == 2  # base instances survive an autoscale/fallback-only overlay


def test_new_instance_in_existing_group_is_appended():
    overlay = {"LLM_engines": {"Qwen3-0.6B": {"instances": [{"id": "c", "port": 8006}]}}}
    merged = merge_into(BASE, overlay)
    ids = {i["id"] for i in merged["LLM_engines"]["Qwen3-0.6B"]["instances"]}
    assert ids == {"a", "b", "c"}


def test_overlay_overrides_existing_instance_by_id():
    overlay = {"LLM_engines": {"Qwen3-0.6B": {"instances": [
        {"id": "a", "host": "localhost", "port": 9999, "cuda_device": 1}]}}}
    merged = merge_into(BASE, overlay)
    insts = {i["id"]: i for i in merged["LLM_engines"]["Qwen3-0.6B"]["instances"]}
    assert insts["a"]["port"] == 9999
    assert insts["a"]["cuda_device"] == 1
    assert insts["b"]["port"] == 8004  # untouched
    # base must not be mutated
    assert BASE["LLM_engines"]["Qwen3-0.6B"]["instances"][0]["port"] == 8002


def test_overlay_model_config_keys_override_base():
    overlay = {"LLM_engines": {"Qwen3-0.6B": {
        "instances": [{"id": "a", "port": 8002}],
        "model_config": {"model_tag": "Qwen/Qwen3-0.6B", "gpu_memory_utilization": 0.8},
    }}}
    merged = merge_into(BASE, overlay)
    cfg = merged["LLM_engines"]["Qwen3-0.6B"]["model_config"]
    assert cfg["gpu_memory_utilization"] == 0.8   # overridden
    assert cfg["max_model_len"] == 500            # base key preserved


def test_overlay_merges_embedding_model_params():
    base = {
        "server": {"port": 8887},
        "embedding_server": {
            "port": 8005,
            "embedding_models": {"m3e": {"model_name": "moka/m3e", "max_length": 512, "use_gpu": True}},
            "reranking_models": {"bge": {"model_name": "BAAI/bge"}},
        },
    }
    overlay = {"embedding_server": {"embedding_models": {"m3e": {"max_length": 256, "use_gpu": False}}}}
    merged = merge_into(base, overlay)
    m3e = merged["embedding_server"]["embedding_models"]["m3e"]
    assert m3e["max_length"] == 256          # overridden
    assert m3e["use_gpu"] is False           # overridden
    assert m3e["model_name"] == "moka/m3e"   # base key preserved
    # base untouched
    assert base["embedding_server"]["embedding_models"]["m3e"]["max_length"] == 512


def test_config_owns_reads_base_yaml(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "server:\n  port: 8887\n"
        "LLM_engines:\n  G:\n    instances:\n      - id: a\n        port: 8002\n"
        "    model_config:\n      model_tag: org/m\n",
        encoding="utf-8",
    )
    assert config_owns("G", "a", str(cfg)) is True
    assert config_owns("G", "zzz", str(cfg)) is False
    assert config_owns("nope", "a", str(cfg)) is False

import pytest

from src.llm_router.overlay import merge_into

pytestmark = pytest.mark.unit


def test_overlay_merges_model_config_for_existing_group():
    """A UI edit to a config.yaml group (e.g. an added LoRA) must reach the
    router, or it 404s requests the backend-launched vLLM can serve."""
    base = {
        "LLM_engines": {
            "Qwen3-0.6B": {
                "instances": [{"id": "qwen3", "port": 8002}],
                "model_config": {
                    "model_tag": "Qwen/Qwen3-0.6B",
                    "lora_modules": [{"name": "lora-a", "path": "/lora/a"}],
                },
            }
        }
    }
    overlay = {
        "LLM_engines": {
            "Qwen3-0.6B": {
                "instances": [{"id": "qwen3", "port": 8002}],
                "model_config": {
                    "model_tag": "Qwen/Qwen3-0.6B",
                    "lora_modules": [
                        {"name": "lora-a", "path": "/lora/a"},
                        {"name": "lora-b", "path": "/lora/b"},
                    ],
                },
            }
        }
    }
    merged = merge_into(base, overlay)
    loras = merged["LLM_engines"]["Qwen3-0.6B"]["model_config"]["lora_modules"]
    assert [m["name"] for m in loras] == ["lora-a", "lora-b"]
    # base is untouched (deep-copied).
    assert len(base["LLM_engines"]["Qwen3-0.6B"]["model_config"]["lora_modules"]) == 1


def test_overlay_instance_override_by_id_and_append():
    base = {"LLM_engines": {"G": {"instances": [{"id": "a", "port": 8000}], "model_config": {}}}}
    overlay = {
        "LLM_engines": {
            "G": {
                "instances": [{"id": "a", "port": 9000}, {"id": "b", "port": 9001}],
            }
        }
    }
    insts = merge_into(base, overlay)["LLM_engines"]["G"]["instances"]
    by_id = {i["id"]: i["port"] for i in insts}
    assert by_id == {"a": 9000, "b": 9001}  # 'a' overridden, 'b' appended


def test_overlay_adds_new_group_whole():
    base = {"LLM_engines": {}}
    overlay = {"LLM_engines": {"New": {"instances": [{"id": "x", "port": 1}], "model_config": {}}}}
    assert "New" in merge_into(base, overlay)["LLM_engines"]

import pytest

from src.llm_router.lora import iter_models, resolve_model

pytestmark = pytest.mark.unit

CONFIG = {
    "LLM_engines": {
        "Llama": {
            "instances": [{"id": "a", "port": 8000}],
            "model_config": {
                "model_tag": "meta-llama/Llama-3.2-3B-Instruct",
                "lora_modules": [
                    {"name": "sql-lora", "path": "repo/sql"},
                    {"name": "fin-lora", "path": "/models/fin"},
                ],
            },
        },
        "Qwen": {
            "instances": [{"id": "q", "port": 8002}],
            "model_config": {"model_tag": "Qwen/Qwen3-0.6B"},
        },
    }
}


def test_resolve_base_group_rewrites_to_tag():
    r = resolve_model(CONFIG, "Llama")
    assert r["route_key"] == "Llama"
    assert r["forward_name"] == "meta-llama/Llama-3.2-3B-Instruct"
    assert r["is_lora"] is False


def test_resolve_lora_keeps_served_name_routes_over_base():
    r = resolve_model(CONFIG, "sql-lora")
    assert r["route_key"] == "Llama"  # routes over the base group's instances
    assert r["forward_name"] == "sql-lora"  # NOT rewritten to the base tag
    assert r["is_lora"] is True
    assert r["model_cfg"]["instances"][0]["port"] == 8000


def test_resolve_unknown_returns_none():
    assert resolve_model(CONFIG, "ghost") is None


def test_iter_models_lists_bases_and_loras_with_parent():
    out = iter_models(CONFIG)
    ids = {m["id"]: m for m in out}
    assert ids["Llama"].get("parent") is None
    assert ids["sql-lora"]["parent"] == "Llama"
    assert ids["fin-lora"]["parent"] == "Llama"
    assert ids["Qwen"].get("parent") is None

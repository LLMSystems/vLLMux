import os
import sys

import pytest
from pydantic import ValidationError

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_ROOT not in sys.path:
    sys.path.insert(0, PKG_ROOT)

import schema  # noqa: E402


def test_canonical_config_validates():
    cfg = schema.load_config()  # the shipped config.yaml
    assert cfg.server.port == 8887
    assert "Qwen3-0.6B" in cfg.LLM_engines


def test_engine_model_config_aliases_to_settings():
    cfg = schema.load_config()
    engine = cfg.LLM_engines["Qwen3-0.6B"]
    assert engine.settings.model_tag == "Qwen/Qwen3-0.6B"
    assert engine.settings.max_model_len == 500
    assert len(engine.instances) == 4
    assert engine.instances[0].id == "qwen3"


def test_embedding_and_reranking_models_parsed():
    cfg = schema.load_config()
    assert set(cfg.embedding_server.embedding_models) == {"m3e-base", "bge-m3"}
    assert "bge-reranker-large" in cfg.embedding_server.reranking_models


def test_missing_required_server_field_raises():
    bad = {"LLM_engines": {}}  # no `server`
    with pytest.raises(ValidationError):
        schema.RootConfig.model_validate(bad)


def test_extra_vllm_flags_are_preserved():
    raw = {
        "server": {"port": 8887},
        "LLM_engines": {
            "M": {
                "instances": [{"id": "a", "port": 8002}],
                "model_config": {"model_tag": "x", "quantization": "gptq"},
            }
        },
    }
    cfg = schema.RootConfig.model_validate(raw)
    # extra="allow" keeps unknown flags accessible.
    assert cfg.LLM_engines["M"].settings.quantization == "gptq"

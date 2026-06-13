import textwrap

import pytest

from src.llm_router.config_loader import load_config

pytestmark = pytest.mark.unit


def _write(tmp_path, body):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def test_load_config_validates_and_returns_dict(tmp_path):
    path = _write(
        tmp_path,
        """
        server:
          host: 0.0.0.0
          port: 8887
        LLM_engines:
          Qwen3-0.6B:
            instances:
              - id: qwen3
                host: localhost
                port: 8002
            model_config:
              model_tag: Qwen/Qwen3-0.6B
        """,
    )
    cfg = load_config(path)
    assert cfg["server"]["port"] == 8887
    # The per-engine alias `model_config` is preserved in the returned dict.
    engine = cfg["LLM_engines"]["Qwen3-0.6B"]
    assert engine["model_config"]["model_tag"] == "Qwen/Qwen3-0.6B"
    assert engine["instances"][0]["port"] == 8002


def test_load_config_rejects_invalid_config(tmp_path):
    # `server.port` is required by the schema -> validation error.
    path = _write(tmp_path, "server:\n  host: 0.0.0.0\n")
    with pytest.raises(Exception):
        load_config(path)


def test_absent_embedding_server_key_is_omitted(tmp_path):
    path = _write(
        tmp_path,
        """
        server:
          port: 8887
        """,
    )
    cfg = load_config(path)
    # Callers rely on .get("embedding_server", {}); the key must be absent, not None.
    assert "embedding_server" not in cfg

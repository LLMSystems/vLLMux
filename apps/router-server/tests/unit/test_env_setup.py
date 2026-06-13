import os

import pytest

from src.llm_router import env as env_mod

pytestmark = pytest.mark.unit

MANAGED = [
    "VLLM_WORKER_MULTIPROC_METHOD",
    "VLLM_USE_V2_MODEL_RUNNER",
    "VLLM_USE_FLASHINFER_SAMPLER",
    "VLLM_ATTENTION_BACKEND",
    "LLM_ROUTER_VLLM_COMPAT",
]


@pytest.fixture(autouse=True)
def isolate_env():
    saved = dict(os.environ)
    for k in MANAGED:
        os.environ.pop(k, None)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_router_env_setup_parity_compat_on(monkeypatch):
    monkeypatch.setattr(env_mod, "_is_wsl", lambda: False)
    os.environ["LLM_ROUTER_VLLM_COMPAT"] = "on"
    env_mod.env_setup()
    assert os.environ["VLLM_WORKER_MULTIPROC_METHOD"] == "spawn"
    assert os.environ["VLLM_USE_V2_MODEL_RUNNER"] == "0"
    assert os.environ["VLLM_ATTENTION_BACKEND"] == "FLASH_ATTN"


def test_router_env_setup_off_is_noop(monkeypatch):
    monkeypatch.setattr(env_mod, "_is_wsl", lambda: True)
    os.environ["LLM_ROUTER_VLLM_COMPAT"] = "off"
    env_mod.env_setup()
    assert "VLLM_ATTENTION_BACKEND" not in os.environ

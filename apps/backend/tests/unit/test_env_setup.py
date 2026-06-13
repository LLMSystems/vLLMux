import os

import pytest

from app.launcher import env as env_mod

pytestmark = pytest.mark.unit

MANAGED = [
    "VLLM_WORKER_MULTIPROC_METHOD",
    "VLLM_USE_V2_MODEL_RUNNER",
    "VLLM_USE_FLASHINFER_SAMPLER",
    "VLLM_ATTENTION_BACKEND",
    "LLM_ROUTER_VLLM_COMPAT",
]

COMPAT_VARS = [
    "VLLM_USE_V2_MODEL_RUNNER",
    "VLLM_USE_FLASHINFER_SAMPLER",
    "VLLM_ATTENTION_BACKEND",
]


@pytest.fixture(autouse=True)
def isolate_env():
    """Fully snapshot/restore os.environ so env_setup()'s writes don't leak."""
    saved = dict(os.environ)
    for k in MANAGED:
        os.environ.pop(k, None)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_spawn_method_always_set():
    env_mod.env_setup()
    assert os.environ["VLLM_WORKER_MULTIPROC_METHOD"] == "spawn"


def test_compat_off_applies_nothing_even_on_wsl(monkeypatch):
    monkeypatch.setattr(env_mod, "_is_wsl", lambda: True)
    os.environ["LLM_ROUTER_VLLM_COMPAT"] = "off"
    env_mod.env_setup()
    assert all(v not in os.environ for v in COMPAT_VARS)


def test_compat_on_sets_all_compat_vars(monkeypatch):
    monkeypatch.setattr(env_mod, "_is_wsl", lambda: False)  # forced on regardless
    os.environ["LLM_ROUTER_VLLM_COMPAT"] = "on"
    env_mod.env_setup()
    assert os.environ["VLLM_USE_V2_MODEL_RUNNER"] == "0"
    assert os.environ["VLLM_USE_FLASHINFER_SAMPLER"] == "0"
    assert os.environ["VLLM_ATTENTION_BACKEND"] == "FLASH_ATTN"


def test_auto_applies_on_wsl(monkeypatch):
    monkeypatch.setattr(env_mod, "_is_wsl", lambda: True)
    # LLM_ROUTER_VLLM_COMPAT unset -> defaults to "auto"
    env_mod.env_setup()
    assert os.environ["VLLM_ATTENTION_BACKEND"] == "FLASH_ATTN"


def test_auto_skips_off_wsl(monkeypatch):
    monkeypatch.setattr(env_mod, "_is_wsl", lambda: False)
    env_mod.env_setup()
    assert all(v not in os.environ for v in COMPAT_VARS)


def test_explicit_export_is_not_overridden(monkeypatch):
    monkeypatch.setattr(env_mod, "_is_wsl", lambda: True)
    os.environ["LLM_ROUTER_VLLM_COMPAT"] = "on"
    os.environ["VLLM_ATTENTION_BACKEND"] = "XFORMERS"  # caller's choice
    env_mod.env_setup()
    assert os.environ["VLLM_ATTENTION_BACKEND"] == "XFORMERS"

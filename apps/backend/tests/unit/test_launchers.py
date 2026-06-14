import pytest

from app.llmops.launchers import (EMBEDDING_KEY, EmbeddingLauncher,
                                  VllmLauncher, _write_effective_config,
                                  build_vllm_cli_args)
from app.llmops.state import ModelKind
from tests.conftest import FAKE_CONFIG

pytestmark = pytest.mark.unit


def test_effective_config_round_trips_through_schema():
    """The dumped effective config must reload through the same schema the
    embedding server uses (by_alias keeps the `model_config` key)."""
    from schema import load_config

    path = _write_effective_config(FAKE_CONFIG)
    reloaded = load_config(path)  # raises if the dump isn't valid RootConfig
    assert "Qwen3-0.6B" in reloaded.LLM_engines
    assert reloaded.embedding_server.port == 8005


def test_embedding_spec_uses_effective_config(tmp_path):
    spec = EmbeddingLauncher().build_spec(FAKE_CONFIG, "config.yaml", EMBEDDING_KEY)
    # --config points at the generated effective file, not the on-disk path.
    cfg_arg = spec.command[spec.command.index("--config") + 1]
    assert cfg_arg.endswith("llmops_effective_config.yaml")


def test_build_vllm_cli_args_flag_formatting():
    args = build_vllm_cli_args(
        {
            "model_tag": "org/m",
            "dtype": "float16",
            "trust_remote_code": True,
            "disabled_flag": False,
            "skip_me": None,
            "mid_tokens": [1, 2],
        }
    )
    assert args[:2] == ["serve", "org/m"]
    assert "--dtype" in args and args[args.index("--dtype") + 1] == "float16"
    assert "--trust-remote-code" in args        # bool True -> presence only
    assert "--disabled-flag" not in args        # bool False -> omitted
    assert "--skip-me" not in args              # None -> omitted
    assert "--mid-tokens" in args and args[args.index("--mid-tokens") + 1] == "[1, 2]"


def test_build_vllm_cli_args_requires_model_tag():
    with pytest.raises(ValueError):
        build_vllm_cli_args({"dtype": "float16"})


def test_vllm_launcher_keys_enumerates_instances():
    keys = VllmLauncher().keys(FAKE_CONFIG)
    assert keys == ["Qwen3-0.6B::qwen3", "Qwen3-0.6B::qwen3-2"]


def test_vllm_launcher_spec_merges_and_sets_cuda():
    spec = VllmLauncher().build_spec(FAKE_CONFIG, "config.yaml", "Qwen3-0.6B::qwen3")
    assert spec.kind == ModelKind.LLM
    assert spec.command[0] == "vllm"
    assert spec.command[1:3] == ["serve", "Qwen/Qwen3-0.6B"]
    # cuda_device popped into env, not passed as a CLI flag (tp == 1).
    assert spec.env["CUDA_VISIBLE_DEVICES"] == "0"
    assert "--cuda-device" not in spec.command
    assert "--id" not in spec.command            # instance id is dropped
    assert spec.probe_url == "http://localhost:8002/health"
    assert spec.port == 8002


def test_embedding_launcher_keys_and_spec():
    launcher = EmbeddingLauncher()
    assert launcher.keys(FAKE_CONFIG) == [EMBEDDING_KEY]
    spec = launcher.build_spec(FAKE_CONFIG, "config.yaml", EMBEDDING_KEY)
    assert spec.kind == ModelKind.EMBEDDING
    assert "--config" in spec.command
    assert spec.env["CUDA_VISIBLE_DEVICES"] == "1"
    assert "PYTHONPATH" in spec.env
    assert spec.probe_url == "http://localhost:8005/health"

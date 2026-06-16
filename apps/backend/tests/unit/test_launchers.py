import json

import pytest

from app.llmops.launchers import (EMBEDDING_KEY, EmbeddingLauncher,
                                  VllmLauncher, _write_effective_config,
                                  build_vllm_cli_args)
from app.llmops.state import ModelKind
from schema import RootConfig
from tests.conftest import FAKE_CONFIG

pytestmark = pytest.mark.unit


def _lora_config() -> RootConfig:
    return RootConfig.model_validate(
        {
            "server": {"host": "0.0.0.0", "port": 8887},
            "LLM_engines": {
                "Llama": {
                    "instances": [{"id": "a", "host": "localhost", "port": 8000, "cuda_device": 0}],
                    "model_config": {
                        "model_tag": "meta-llama/Llama-3.2-3B-Instruct",
                        "enable_lora": True,
                        "allow_runtime_lora": True,
                        "lora_modules": [{"name": "sql", "path": "repo/sql"}],
                    },
                }
            },
        }
    )


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


def test_build_vllm_cli_args_lora_modules_multi_value():
    args = build_vllm_cli_args(
        {
            "model_tag": "org/m",
            "enable_lora": True,
            "max_lora_rank": 16,
            "allow_runtime_lora": True,  # env, not a CLI flag
            "lora_modules": [
                {"name": "sql", "path": "repo/sql"},
                {"name": "fin", "path": "/models/fin", "base_model_name": "org/m"},
            ],
        }
    )
    assert "--enable-lora" in args
    assert args[args.index("--max-lora-rank") + 1] == "16"
    # allow_runtime_lora must NOT leak into the CLI (handled as env).
    assert "--allow-runtime-lora" not in args
    # --lora-modules followed by one JSON value per adapter.
    i = args.index("--lora-modules")
    assert json.loads(args[i + 1]) == {"name": "sql", "path": "repo/sql"}
    assert json.loads(args[i + 2])["base_model_name"] == "org/m"


def test_enable_lora_injects_default_max_loras():
    args = build_vllm_cli_args({"model_tag": "org/m", "enable_lora": True})
    assert "--max-loras" in args and args[args.index("--max-loras") + 1] == "4"


def test_explicit_max_loras_wins_over_default():
    args = build_vllm_cli_args({"model_tag": "org/m", "enable_lora": True, "max_loras": 8})
    assert args[args.index("--max-loras") + 1] == "8"


def test_no_max_loras_when_lora_disabled():
    args = build_vllm_cli_args({"model_tag": "org/m"})
    assert "--max-loras" not in args


def test_vllm_launcher_sets_runtime_lora_env():
    spec = VllmLauncher().build_spec(_lora_config(), "config.yaml", "Llama::a")
    assert spec.env.get("VLLM_ALLOW_RUNTIME_LORA_UPDATING") == "True"
    assert "--enable-lora" in spec.command
    i = spec.command.index("--lora-modules")
    assert json.loads(spec.command[i + 1])["name"] == "sql"


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

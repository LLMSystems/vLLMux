import json

import pytest

from app.llmops.launchers import (CAP_LORA_MODULES, CAP_METRICS_SGLANG,
                                  CAP_RUNTIME_LORA, CAP_SLEEP, EMBEDDING_KEY,
                                  ENGINE_DEFAULT, EmbeddingLauncher, SglangLauncher,
                                  VllmLauncher, _write_effective_config,
                                  build_sglang_cli_args, build_vllm_cli_args)
from app.llmops.state import ModelKind
from schema import RootConfig
from tests.conftest import FAKE_CONFIG

pytestmark = pytest.mark.unit


def _engine_config(engine: str) -> RootConfig:
    """A one-group LLM config whose engine is `engine` (no field = default)."""
    mc = {"model_tag": "org/m"}
    if engine is not None:
        mc["engine"] = engine
    return RootConfig.model_validate({
        "server": {"host": "0.0.0.0", "port": 8887},
        "LLM_engines": {"G": {
            "instances": [{"id": "a", "host": "localhost", "port": 8000}],
            "model_config": mc,
        }},
    })


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
    assert "--disabled-flag" not in args        # bool False -> --no- variant
    assert "--no-disabled-flag" in args         # explicit off (BooleanOptionalAction)
    assert "--skip-me" not in args              # None -> omitted
    assert "--mid-tokens" in args and args[args.index("--mid-tokens") + 1] == "[1, 2]"


def test_build_vllm_cli_args_requires_model_tag():
    with pytest.raises(ValueError):
        build_vllm_cli_args({"dtype": "float16"})


def test_kv_transfer_config_emitted_as_single_json_arg():
    # Nested-dict engine args (e.g. --kv-transfer-config) must become one valid
    # JSON value, not Python's str(dict) (single quotes vLLM can't parse).
    cfg = {
        "kv_connector": "OffloadingConnector",
        "kv_role": "kv_both",
        "kv_connector_extra_config": {"spec_name": "TieringOffloadingSpec"},
    }
    args = build_vllm_cli_args({"model_tag": "org/m", "kv_transfer_config": cfg})
    assert "--kv-transfer-config" in args
    payload = args[args.index("--kv-transfer-config") + 1]
    assert json.loads(payload) == cfg          # valid JSON, round-trips
    assert "'" not in payload                  # not a Python repr


def test_kv_transfer_config_sets_pythonhashseed():
    # Cross-instance KV sharing needs an identical hash seed on every process.
    cfg = RootConfig.model_validate(
        {
            "server": {"host": "0.0.0.0", "port": 8887},
            "LLM_engines": {
                "M": {
                    "instances": [{"id": "a", "host": "localhost", "port": 8000, "cuda_device": 0}],
                    "model_config": {
                        "model_tag": "org/m",
                        "kv_transfer_config": {"kv_connector": "OffloadingConnector"},
                    },
                }
            },
        }
    )
    spec = VllmLauncher().build_spec(cfg, "config.yaml", "M::a")
    assert spec.env.get("PYTHONHASHSEED") == "0"


def test_no_pythonhashseed_without_kv_transfer():
    spec = VllmLauncher().build_spec(FAKE_CONFIG, "config.yaml", "Qwen3-0.6B::qwen3")
    assert "PYTHONHASHSEED" not in spec.env


def test_sleep_mode_sets_dev_mode_env_and_flag():
    cfg = RootConfig.model_validate(
        {
            "server": {"host": "0.0.0.0", "port": 8887},
            "LLM_engines": {
                "M": {
                    "instances": [{"id": "a", "host": "localhost", "port": 8000, "cuda_device": 0}],
                    "model_config": {"model_tag": "org/m", "enable_sleep_mode": True},
                }
            },
        }
    )
    spec = VllmLauncher().build_spec(cfg, "config.yaml", "M::a")
    assert spec.sleep_enabled is True
    assert spec.env.get("VLLM_SERVER_DEV_MODE") == "1"
    assert "--enable-sleep-mode" in spec.command  # emitted by the CLI arg builder


def _host_arg(cmd):
    return cmd[cmd.index("--host") + 1]


def test_vllm_binds_configured_host_by_default():
    spec = VllmLauncher().build_spec(FAKE_CONFIG, "config.yaml", "Qwen3-0.6B::qwen3")
    # Default: bind the configured host (localhost); probe + record match it.
    assert _host_arg(spec.command) == spec.host == "localhost"
    assert spec.probe_url == "http://localhost:8002/health"


def test_vllm_bind_host_env_overrides_only_the_bind_address(monkeypatch):
    # HA split deploys: LLMOPS_VLLM_BIND_HOST binds vLLM to a routable interface
    # (--host), but the probe + recorded host (what the local backend uses) stay
    # the configured localhost — routers reach it via the advertised NODE_HOST.
    monkeypatch.setenv("LLMOPS_VLLM_BIND_HOST", "0.0.0.0")
    spec = VllmLauncher().build_spec(FAKE_CONFIG, "config.yaml", "Qwen3-0.6B::qwen3")
    assert _host_arg(spec.command) == "0.0.0.0"      # vLLM listens on all interfaces
    assert spec.host == "localhost"                  # record unchanged
    assert spec.probe_url == "http://localhost:8002/health"  # local probe unchanged


def test_no_sleep_mode_by_default():
    spec = VllmLauncher().build_spec(FAKE_CONFIG, "config.yaml", "Qwen3-0.6B::qwen3")
    assert spec.sleep_enabled is False
    assert "VLLM_SERVER_DEV_MODE" not in spec.env
    assert "--enable-sleep-mode" not in spec.command


def test_routing_strategy_not_passed_to_vllm():
    # routing_strategy is a router-only knob riding the shared model_config; it
    # must never reach `vllm serve` (vLLM errors on the unknown arg).
    args = build_vllm_cli_args(
        {"model_tag": "org/m", "dtype": "float16", "routing_strategy": "session_affinity"}
    )
    assert "--routing-strategy" not in args
    assert "session_affinity" not in args
    assert "--dtype" in args  # other flags still pass through


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


# ---- multi-engine abstraction (docs/multi-backend-engine-design_zh-CN.md) ----

def test_engine_defaults_to_vllm_when_unset():
    """A config with no `engine` field is all vLLM = historical behaviour."""
    cfg = _engine_config(engine=None)
    assert cfg.LLM_engines["G"].settings.engine == "vllm"


def test_vllm_launcher_claims_only_its_engine():
    """keys() returns groups for this launcher's engine; a non-vllm group is
    skipped so a future SGLang launcher can claim it instead."""
    v = VllmLauncher()
    assert v.engine == "vllm"
    assert v.keys(_engine_config(None)) == ["G::a"]       # default
    assert v.keys(_engine_config("vllm")) == ["G::a"]     # explicit
    assert v.keys(_engine_config("sglang")) == []         # not mine


def test_vllm_launcher_declares_capabilities_on_spec():
    v = VllmLauncher()
    spec = v.build_spec(_engine_config("vllm"), "config.yaml", "G::a")
    assert spec.engine == "vllm"
    assert CAP_SLEEP in spec.capabilities
    assert spec.capabilities == v.capabilities


def test_embedding_launcher_registers_under_default_engine():
    e = EmbeddingLauncher()
    assert e.engine == ENGINE_DEFAULT
    assert e.capabilities == frozenset()
    spec = e.build_spec(FAKE_CONFIG, "config.yaml", EMBEDDING_KEY)
    assert spec.engine == ENGINE_DEFAULT


def test_engine_is_never_passed_to_vllm_cli():
    """`engine` is launcher-meta; it must not reach `vllm serve` (unknown arg)."""
    args = build_vllm_cli_args({"model_tag": "org/m", "engine": "vllm", "kind": "chat"})
    assert "--engine" not in args
    assert "--kind" not in args


# ---- SGLang launcher (docs/multi-backend-engine-design_zh-CN.md §5.2) --------

def _sglang_config(extra: dict | None = None) -> RootConfig:
    mc = {"model_tag": "Qwen/Qwen3-0.6B", "engine": "sglang"}
    if extra:
        mc.update(extra)
    return RootConfig.model_validate({
        "server": {"host": "0.0.0.0", "port": 8887},
        "LLM_engines": {"S": {
            "instances": [{"id": "a", "host": "localhost", "port": 8100, "cuda_device": 2}],
            "model_config": mc,
        }},
    })


def test_sglang_args_model_path_and_served_name():
    args = build_sglang_cli_args({"model_tag": "Qwen/Qwen3-0.6B"})
    assert args[:2] == ["--model-path", "Qwen/Qwen3-0.6B"]
    # served-model-name defaults to model_tag so /v1/models + forward_name are stable.
    assert "--served-model-name" in args
    assert args[args.index("--served-model-name") + 1] == "Qwen/Qwen3-0.6B"


def test_sglang_args_translate_typed_params():
    # The three common params have different SGLang flag names.
    args = build_sglang_cli_args({
        "model_tag": "org/m", "max_model_len": 4096,
        "gpu_memory_utilization": 0.45, "tensor_parallel_size": 2,
    })
    assert args[args.index("--context-length") + 1] == "4096"
    assert args[args.index("--mem-fraction-static") + 1] == "0.45"
    assert args[args.index("--tp-size") + 1] == "2"
    # vLLM names must NOT appear.
    assert "--max-model-len" not in args
    assert "--gpu-memory-utilization" not in args


def test_sglang_args_bool_is_store_true_no_dual():
    args = build_sglang_cli_args({
        "model_tag": "org/m", "disable_radix_cache": True, "skip_server_warmup": False,
    })
    assert "--disable-radix-cache" in args          # True -> present
    assert "--skip-server-warmup" not in args       # False -> omitted
    assert "--no-skip-server-warmup" not in args    # never synthesise a --no- dual


def test_sglang_args_skip_router_only_keys():
    args = build_sglang_cli_args(
        {"model_tag": "org/m", "engine": "sglang", "kind": "chat",
         "routing_strategy": "session_affinity", "dtype": "bfloat16"})
    assert "--engine" not in args and "--kind" not in args
    assert "--routing-strategy" not in args
    assert args[args.index("--dtype") + 1] == "bfloat16"   # real flags still pass through


def test_sglang_args_explicit_served_name_wins():
    args = build_sglang_cli_args({"model_tag": "org/m", "served_model_name": "my-name"})
    assert args[args.index("--served-model-name") + 1] == "my-name"


def test_sglang_launcher_claims_only_sglang_engine():
    s = SglangLauncher()
    assert s.kind == ModelKind.LLM and s.engine == "sglang"
    assert s.keys(_sglang_config()) == ["S::a"]
    assert s.keys(FAKE_CONFIG) == []                # FAKE_CONFIG is all vLLM
    assert VllmLauncher().keys(_sglang_config()) == []  # vLLM doesn't claim sglang group


def test_sglang_launcher_capabilities():
    # Runtime + static LoRA and sglang:* metrics are wired; sleep is absent in
    # SGLang (autoscaler degrades to ready<->stopped).
    caps = SglangLauncher().capabilities
    assert CAP_RUNTIME_LORA in caps and CAP_LORA_MODULES in caps
    assert CAP_METRICS_SGLANG in caps
    assert CAP_SLEEP not in caps


def test_sglang_args_always_enable_metrics():
    # /metrics must be on (vLLM exposes it by default; SGLang needs the flag) so the
    # router can scrape sglang:* for the autoscaler.
    args = build_sglang_cli_args({"model_tag": "org/m"})
    assert "--enable-metrics" in args


def test_sglang_args_lora_enable_and_paths():
    # enable_lora -> --enable-lora; static lora_modules -> SGLang NAME=PATH form.
    args = build_sglang_cli_args({
        "model_tag": "org/m", "enable_lora": True,
        "lora_modules": [{"name": "sql", "path": "/lora/sql"},
                         {"name": "math", "path": "/lora/math"}],
        "max_lora_rank": 16,
    })
    assert "--enable-lora" in args
    i = args.index("--lora-paths")
    assert args[i + 1] == "sql=/lora/sql" and args[i + 2] == "math=/lora/math"
    assert args[args.index("--max-lora-rank") + 1] == "16"   # other lora knobs pass through
    assert "--lora-modules" not in args                       # not vLLM's JSON form


def test_sglang_args_runtime_lora_toggle_enables_lora():
    # Our runtime toggle (allow_runtime_lora) must turn on --enable-lora so SGLang
    # accepts POST /load_lora_adapter, even with no static modules.
    args = build_sglang_cli_args({"model_tag": "org/m", "allow_runtime_lora": True})
    assert "--enable-lora" in args
    assert "--allow-runtime-lora" not in args                 # the toggle is not a flag


def test_sglang_args_no_lora_by_default():
    args = build_sglang_cli_args({"model_tag": "org/m"})
    assert "--enable-lora" not in args and "--lora-paths" not in args


def test_sglang_build_spec():
    spec = SglangLauncher().build_spec(_sglang_config({"max_model_len": 4096}), "config.yaml", "S::a")
    assert spec.engine == "sglang"
    assert spec.command[1:3] == ["-m", "sglang.launch_server"]
    assert "--model-path" in spec.command
    assert spec.command[spec.command.index("--context-length") + 1] == "4096"
    # single-GPU cuda_device -> env, not a CLI flag; id dropped.
    assert spec.env["CUDA_VISIBLE_DEVICES"] == "2"
    assert "--cuda-device" not in spec.command and "--id" not in spec.command
    assert spec.probe_url == "http://localhost:8100/health"
    assert spec.host == "localhost" and spec.port == 8100
    assert "--host" in spec.command and spec.command[spec.command.index("--host") + 1] == "localhost"


def test_sglang_bind_host_env_overrides_only_the_bind_address(monkeypatch):
    # Cross-container HA: LLMOPS_VLLM_BIND_HOST binds sglang to 0.0.0.0 (--host), but
    # the probe + recorded host stay localhost; routers reach it via NODE_HOST.
    monkeypatch.setenv("LLMOPS_VLLM_BIND_HOST", "0.0.0.0")
    spec = SglangLauncher().build_spec(_sglang_config(), "config.yaml", "S::a")
    assert spec.command[spec.command.index("--host") + 1] == "0.0.0.0"  # binds all
    assert spec.host == "localhost"                                     # record unchanged
    assert spec.probe_url == "http://localhost:8100/health"            # local probe unchanged


def test_sglang_binds_configured_host_by_default():
    spec = SglangLauncher().build_spec(_sglang_config(), "config.yaml", "S::a")
    assert spec.command[spec.command.index("--host") + 1] == "localhost"

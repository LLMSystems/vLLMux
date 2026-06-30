"""Per-kind launch recipes: turn validated config into a LaunchSpec.

A Launcher knows how to (a) enumerate the instance keys a config defines for its
kind, and (b) build the LaunchSpec for one key. Spawning/killing is delegated to
process.py, so launchers stay pure (no subprocess side effects) and unit-testable.

Two implementations:
  - VllmLauncher: `vllm serve <tag> --flags...`, one process per instance.
  - EmbeddingLauncher: the router-server's embedding_reranker_launcher.py, one
    process for the whole embedding/reranking server.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from typing import Protocol

import yaml

from app.llmops.instance import LaunchSpec
from app.llmops.state import ModelKind

logger = logging.getLogger(__name__)

# Where the merged (config.yaml + overlay) config is dumped for the embedding
# server, which reads a config file directly and so wouldn't otherwise see
# overlay edits. Overwritten per launch; the script reads it once at startup.
_EFFECTIVE_CONFIG_PATH = os.path.join(tempfile.gettempdir(), "llmops_effective_config.yaml")


def _write_effective_config(config) -> str:
    """Dump the merged RootConfig to YAML so the embedding server sees overlay
    edits. by_alias keeps the historical `model_config` key the schema expects."""
    data = config.model_dump(by_alias=True)
    with open(_EFFECTIVE_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return _EFFECTIVE_CONFIG_PATH

# apps/backend/app/llmops/launchers.py -> apps/router-server is 3 dirs up + sibling.
_ROUTER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "router-server")
)
_EMBEDDING_SCRIPT = os.path.join(
    _ROUTER_ROOT, "src", "embedding_reranker", "embedding_reranker_launcher.py"
)

EMBEDDING_KEY = "embedding::default"
LOG_DIR = "./logs"


# Keys consumed as env vars / handled specially, not emitted as CLI flags.
_LORA_RUNTIME_KEY = "allow_runtime_lora"
# Router-only knobs that ride the shared model_config (EngineModelConfig is
# extra="allow") but belong to the router, not vLLM — never pass them to
# `vllm serve` or it errors on an unknown argument. `engine` is launcher-meta
# (which backend to run); it too must never reach the vLLM CLI.
_ROUTER_ONLY_KEYS = frozenset({"routing_strategy", "kind", "engine"})
# Everything build_vllm_cli_args must skip (model_tag is the positional arg).
_SKIP_CLI_KEYS = frozenset({"model_tag", _LORA_RUNTIME_KEY}) | _ROUTER_ONLY_KEYS

# vLLM's --max-loras defaults to 1 (only one distinct adapter per batch, which
# serialises mixed-LoRA traffic and leaves no headroom for hot-loading more).
# Whenever LoRA is enabled we inject this instead, unless the config sets its own.
_DEFAULT_MAX_LORAS = 4


def build_vllm_cli_args(model_cfg: dict) -> list[str]:
    """dict -> ``vllm serve`` CLI args. Ported verbatim from the old launcher.

    bool True -> ``--flag``; bool False -> ``--no-flag`` (vLLM's
    BooleanOptionalAction); lists are JSON-encoded; None is skipped; keys are
    kebab-cased. `model_tag` is the positional model argument.

    Two LoRA special cases: ``lora_modules`` (list of {name, path, …}) becomes the
    multi-value ``--lora-modules <json> <json> …`` form vLLM expects (the generic
    list path would collapse it into one wrong arg); ``allow_runtime_lora`` is an
    env toggle (see VllmLauncher), not a CLI flag, so it is skipped here.

    When ``enable_lora`` is set, ``max_loras`` is forced to a sensible default
    (instead of vLLM's 1) unless the config specifies it explicitly.
    """
    model_tag = model_cfg.get("model_tag")
    if not model_tag:
        raise ValueError("model_config must provide 'model_tag'")

    # LoRA headroom: never leave max_loras at vLLM's default of 1 when LoRA is on.
    if model_cfg.get("enable_lora") and "max_loras" not in model_cfg:
        model_cfg = {**model_cfg, "max_loras": _DEFAULT_MAX_LORAS}

    cli_args = ["serve", model_tag]
    for key, value in model_cfg.items():
        if key in _SKIP_CLI_KEYS or value is None:
            continue
        key_flag = "--" + key.replace("_", "-")
        if key == "lora_modules":
            # Drop None-valued keys (model_dump emits base_model_name: null) so the
            # JSON vLLM sees is clean.
            modules = [{k: v for k, v in m.items() if v is not None} for m in value if m]
            modules = [m for m in modules if m.get("name")]
            if not modules:
                continue
            cli_args.append(key_flag)
            # JSON form per module so base_model_name (and any extra keys) survive;
            # vLLM accepts several values after a single --lora-modules.
            cli_args.extend(json.dumps(m, ensure_ascii=False) for m in modules)
        elif isinstance(value, bool):
            # vLLM bool engine args are BooleanOptionalAction: emit --no-<flag> for
            # an explicit False so a feature that's on by default (e.g. prefix
            # caching / chunked prefill in V1) can actually be turned off, rather
            # than silently omitted (which would leave the default in place).
            cli_args.append(key_flag if value else "--no-" + key.replace("_", "-"))
        elif isinstance(value, list):
            cli_args.append(key_flag)
            cli_args.append(json.dumps(value))
        elif isinstance(value, dict):
            # Nested-JSON engine args (e.g. --kv-transfer-config for cross-instance
            # KV sharing) need a single JSON value; str(dict) emits Python single
            # quotes that vLLM's JSON parse rejects.
            cli_args.append(key_flag)
            cli_args.append(json.dumps(value, ensure_ascii=False))
        else:
            cli_args.append(key_flag)
            cli_args.append(str(value))
    return cli_args


# Engine capability flags. Callers gate optional features on *capabilities*, never
# on the engine name (`if "sleep" in caps`, never `if engine == "vllm"`), so adding
# an engine is just declaring its capability set. See docs/multi-backend-engine-design_zh-CN.md §4.
CAP_SLEEP = "sleep"                # /sleep + /wake_up (warm standby, frees VRAM, process stays up)
CAP_RUNTIME_LORA = "runtime_lora"  # runtime LoRA load/unload endpoints
CAP_LORA_MODULES = "lora_modules"  # static --lora-modules at launch
CAP_KV_TRANSFER = "kv_transfer"    # cross-instance KV cache sharing
CAP_METRICS_VLLM = "metrics_vllm"  # exposes vLLM-format Prometheus metrics (waiting queue, …)

# Sentinel engine name for non-LLM launchers (embedding server): they aren't
# selected by an engine choice, so they register under one fixed value.
ENGINE_DEFAULT = "default"


class Launcher(Protocol):
    kind: ModelKind
    # Which engine this launcher serves; dispatch is keyed on (kind, engine).
    engine: str
    # Optional features this engine supports (see CAP_* above). Threaded onto the
    # LaunchSpec so callers (autoscaler, sleep/LoRA APIs, metrics) can gate without
    # re-checking the engine name.
    capabilities: frozenset[str]

    def keys(self, config) -> list[str]:
        """All instance keys this launcher defines in the config (its engine only)."""
        ...

    def build_spec(self, config, config_path: str, key: str) -> LaunchSpec:
        """Resolve the LaunchSpec for one key."""
        ...


class VllmLauncher:
    kind = ModelKind.LLM
    engine = "vllm"
    capabilities = frozenset({
        CAP_SLEEP, CAP_RUNTIME_LORA, CAP_LORA_MODULES, CAP_KV_TRANSFER, CAP_METRICS_VLLM,
    })

    def keys(self, config) -> list[str]:
        out: list[str] = []
        for model_tag, engine in config.LLM_engines.items():
            # Only claim groups configured for this engine. `engine` defaults to
            # "vllm" (EngineModelConfig), so a config with no engine field is all
            # vLLM = today's behaviour.
            if getattr(engine.settings, "engine", "vllm") != self.engine:
                continue
            for inst in engine.instances:
                out.append(f"{model_tag}::{inst.id}")
        return out

    def build_spec(self, config, config_path: str, key: str) -> LaunchSpec:
        model_tag, _, instance_id = key.partition("::")
        engine = config.LLM_engines.get(model_tag)
        if engine is None:
            raise KeyError(f"model group '{model_tag}' not in config")
        inst = next((i for i in engine.instances if i.id == instance_id), None)
        if inst is None:
            raise KeyError(f"instance '{instance_id}' not in group '{model_tag}'")

        # Merge shared model_config with the instance overrides, mirroring the
        # historical behaviour: cuda_device only becomes CUDA_VISIBLE_DEVICES
        # for single-GPU (tp==1); the `id` field is dropped.
        merged: dict = engine.settings.model_dump(by_alias=False)
        merged.update(inst.model_dump())

        env: dict[str, str] = {}
        if merged.get("tensor_parallel_size", 1) == 1:
            cuda_device = merged.pop("cuda_device", None)
            if cuda_device is not None:
                env["CUDA_VISIBLE_DEVICES"] = str(cuda_device)
        merged.pop("id", None)

        # Runtime LoRA load/unload requires this env in addition to --enable-lora
        # (kept out of the CLI args; see build_vllm_cli_args).
        if merged.get(_LORA_RUNTIME_KEY):
            env["VLLM_ALLOW_RUNTIME_LORA_UPDATING"] = "True"

        # Cross-instance KV sharing: every process sharing the same KV store must
        # use an identical hash seed, otherwise the same content yields different
        # block hashes per process and cross-process prefix hits never land.
        if merged.get("kv_transfer_config"):
            env.setdefault("PYTHONHASHSEED", "0")

        # Sleep mode (autoscaling warm-standby tier): the `--enable-sleep-mode`
        # CLI flag is emitted by build_vllm_cli_args from the config, but the
        # /sleep, /wake_up and /is_sleeping endpoints only appear when the server
        # also runs in dev mode (an env toggle, not a CLI flag). See
        # docs/vllm_sleep_mode.md + docs/autoscaling-design_zh-CN.md.
        sleep_enabled = bool(merged.get("enable_sleep_mode"))
        if sleep_enabled:
            env["VLLM_SERVER_DEV_MODE"] = "1"

        # HA split deploys: optionally bind vLLM to a routable interface (e.g.
        # 0.0.0.0) so a router/node-agent in another netns or host can reach it.
        # Only the `--host` *bind* address changes; routers connect via the
        # advertised LLMOPS_NODE_HOST (see reconciler / instances_live), and the
        # local health probe + record keep the configured host (localhost), which a
        # 0.0.0.0 bind also serves. Empty (default) = bind the configured host =
        # today's localhost-only behaviour, byte-for-byte unchanged.
        bind_host = os.environ.get("LLMOPS_VLLM_BIND_HOST", "").strip()
        cli_cfg = {**merged, "host": bind_host} if bind_host else merged
        command = ["vllm"] + build_vllm_cli_args(cli_cfg)
        log_path = os.path.join(LOG_DIR, f"{model_tag}__{instance_id}.log")
        return LaunchSpec(
            key=key,
            kind=self.kind,
            engine=self.engine,
            capabilities=self.capabilities,
            command=command,
            env=env,
            log_path=log_path,
            host=inst.host,
            port=inst.port,
            probe_url=f"http://{inst.host}:{inst.port}/health",
            model_tag=engine.settings.model_tag,
            sleep_enabled=sleep_enabled,
        )


class EmbeddingLauncher:
    kind = ModelKind.EMBEDDING
    engine = ENGINE_DEFAULT  # not engine-selectable; one launcher for the embedding server
    capabilities = frozenset()

    def keys(self, config) -> list[str]:
        emb = config.embedding_server
        if emb and (emb.embedding_models or emb.reranking_models):
            return [EMBEDDING_KEY]
        return []

    def build_spec(self, config, config_path: str, key: str) -> LaunchSpec:
        emb = config.embedding_server
        if emb is None or not (emb.embedding_models or emb.reranking_models):
            raise KeyError("no embedding/reranking models configured")

        env = {"PYTHONPATH": _ROUTER_ROOT}
        if emb.cuda_device is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(emb.cuda_device)

        # Pass the merged effective config so overlay edits take effect. Fall back
        # to the on-disk config.yaml if the dump fails, so a bug here can never
        # stop the embedding server from launching.
        cfg_arg = config_path
        try:
            cfg_arg = _write_effective_config(config)
        except Exception:
            logger.warning("Effective-config dump failed; launching embedding with %s", config_path)
        command = [sys.executable, _EMBEDDING_SCRIPT, "--config", cfg_arg]
        log_path = os.path.join(LOG_DIR, "embedding_server.log")
        return LaunchSpec(
            key=key,
            kind=self.kind,
            engine=self.engine,
            capabilities=self.capabilities,
            command=command,
            env=env,
            log_path=log_path,
            host=emb.host,
            port=emb.port,
            probe_url=f"http://{emb.host}:{emb.port}/health",
        )


# ---- SGLang ---------------------------------------------------------------

# Engine-neutral typed params (EngineModelConfig) -> SGLang flag names. These three
# have different names from vLLM; everything else falls through as a kebab-cased
# --<key> (engine-native passthrough via extra="allow"). See §2.3 of the design doc.
_SGLANG_PARAM_MAP = {
    "max_model_len": "context-length",
    "gpu_memory_utilization": "mem-fraction-static",
    "tensor_parallel_size": "tp-size",
}
# Keys consumed specially (model_tag/served_model_name handled up front; id is the
# key; cuda_device becomes CUDA_VISIBLE_DEVICES) plus the router-only knobs.
_SGLANG_SKIP_CLI_KEYS = frozenset(
    {"model_tag", "served_model_name", "id", "cuda_device", _LORA_RUNTIME_KEY}
) | _ROUTER_ONLY_KEYS


def build_sglang_cli_args(model_cfg: dict) -> list[str]:
    """dict -> ``python -m sglang.launch_server`` CLI args.

    Unlike vLLM: the model is ``--model-path`` (not a positional), and we always
    emit ``--served-model-name`` so ``/v1/models`` (and the router's forward_name)
    is the stable ``model_tag`` rather than SGLang's default of the raw path.

    Bools are SGLang's ``store_true``: a True value emits ``--flag``; a False value
    is *omitted* — there is NO ``--no-flag`` dual (vLLM's BooleanOptionalAction),
    and many SGLang flags are themselves negative (``--disable-radix-cache``), so we
    must not synthesise ``--no-`` forms. The three common params above are
    translated; the rest pass through kebab-cased.
    """
    model_tag = model_cfg.get("model_tag")
    if not model_tag:
        raise ValueError("model_config must provide 'model_tag'")
    served = model_cfg.get("served_model_name") or model_tag

    args = ["--model-path", str(model_tag), "--served-model-name", str(served)]
    for key, value in model_cfg.items():
        if key in _SGLANG_SKIP_CLI_KEYS or value is None:
            continue
        flag = "--" + _SGLANG_PARAM_MAP.get(key, key).replace("_", "-")
        if isinstance(value, bool):
            if value:                       # store_true: True -> present; False -> omit
                args.append(flag)
        elif isinstance(value, list):
            # SGLang multi-value flags take space-separated values (e.g. --lora-paths).
            args.append(flag)
            args.extend(str(v) for v in value)
        elif isinstance(value, dict):
            args.append(flag)
            args.append(json.dumps(value, ensure_ascii=False))
        else:
            args.append(flag)
            args.append(str(value))
    return args


class SglangLauncher:
    kind = ModelKind.LLM
    engine = "sglang"
    # First cut covers launch + route + lifecycle. SGLang *does* have runtime LoRA
    # (POST /load_lora_adapter — note: no /v1 prefix, unlike vLLM) and sglang:* Prometheus
    # metrics, but both need extra wiring (a per-engine LoRA endpoint path + a
    # sglang metrics parser) — declared in a follow-up so we never advertise a
    # capability that isn't end-to-end wired. No sleep (SGLang has no /sleep+/wake_up;
    # `--sleep-on-idle` only lowers CPU, doesn't free VRAM) -> autoscaler degrades to
    # ready<->stopped. See docs/multi-backend-engine-design_zh-CN.md §5.2.
    capabilities = frozenset()

    def keys(self, config) -> list[str]:
        out: list[str] = []
        for model_tag, engine in config.LLM_engines.items():
            if getattr(engine.settings, "engine", "vllm") != self.engine:
                continue
            for inst in engine.instances:
                out.append(f"{model_tag}::{inst.id}")
        return out

    def build_spec(self, config, config_path: str, key: str) -> LaunchSpec:
        model_tag, _, instance_id = key.partition("::")
        engine = config.LLM_engines.get(model_tag)
        if engine is None:
            raise KeyError(f"model group '{model_tag}' not in config")
        inst = next((i for i in engine.instances if i.id == instance_id), None)
        if inst is None:
            raise KeyError(f"instance '{instance_id}' not in group '{model_tag}'")

        # Merge shared model_config with instance overrides, mirroring VllmLauncher:
        # single-GPU cuda_device -> CUDA_VISIBLE_DEVICES; the `id` field is dropped.
        merged: dict = engine.settings.model_dump(by_alias=False)
        merged.update(inst.model_dump())

        env: dict[str, str] = {}
        if merged.get("tensor_parallel_size", 1) == 1:
            cuda_device = merged.pop("cuda_device", None)
            if cuda_device is not None:
                env["CUDA_VISIBLE_DEVICES"] = str(cuda_device)
        merged.pop("id", None)

        command = [sys.executable, "-m", "sglang.launch_server"] + build_sglang_cli_args(merged)
        log_path = os.path.join(LOG_DIR, f"{model_tag}__{instance_id}.log")
        return LaunchSpec(
            key=key,
            kind=self.kind,
            engine=self.engine,
            capabilities=self.capabilities,
            command=command,
            env=env,
            log_path=log_path,
            host=inst.host,
            port=inst.port,
            probe_url=f"http://{inst.host}:{inst.port}/health",
            model_tag=engine.settings.model_tag,
        )

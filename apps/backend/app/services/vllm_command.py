"""Parse a pasted ``vllm serve`` (or ``python -m vllm...``) command into the
structured pieces the dashboard config needs.

The split mirrors the config schema:
  - instance-level fields: id, host, port, cuda_device
  - group-level model_config (settings): model_tag + every other vLLM flag
    (kept verbatim via snake_case keys; the schema's extra='allow' absorbs them
    and launchers.build_vllm_cli_args turns them back into CLI flags)

Pure string→dict; no IO, so it is trivially unit-testable.
"""
from __future__ import annotations

import json
import re
import shlex
from typing import Any

# Flags that describe the instance/endpoint, not the model itself.
_INSTANCE_FLAGS = {"port", "host"}
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _parse_lora_module(value: str) -> dict[str, Any]:
    """One `--lora-modules` value -> {name, path, …}.

    Accepts both vLLM forms: the JSON object (`{"name": .., "path": ..}`) and the
    short `name=path`. Unrecognised input is kept as a name-only stub so nothing
    is silently dropped."""
    text = value.strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and obj.get("name"):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    if "=" in text:
        name, _, path = text.partition("=")
        return {"name": name.strip(), "path": path.strip()}
    return {"name": text, "path": ""}


def _coerce(value: str) -> Any:
    """Best-effort scalar typing so ints/floats/bools round-trip cleanly."""
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "inst"


def parse_vllm_command(command: str) -> dict[str, Any]:
    """Return {group, instance, model_config, warnings} from a vLLM command."""
    warnings: list[str] = []
    if not command or not command.strip():
        raise ValueError("empty command")

    tokens = shlex.split(command, comments=False)

    # 1. Leading env assignments (e.g. CUDA_VISIBLE_DEVICES=1 vllm serve ...).
    env: dict[str, str] = {}
    while tokens and _ENV_ASSIGN.match(tokens[0]):
        k, _, v = tokens[0].partition("=")
        env[k] = v
        tokens = tokens[1:]

    # 2. Strip the executable and locate the model tag.
    model_tag: str | None = None
    rest = tokens
    if "serve" in rest:
        rest = rest[rest.index("serve") + 1 :]
        if rest and not rest[0].startswith("-"):
            model_tag = rest[0]
            rest = rest[1:]
    else:
        # `python -m vllm.entrypoints.openai.api_server --model X ...` form:
        # drop leading non-flag tokens (python, -m, module, ...).
        while rest and not rest[0].startswith("-"):
            rest = rest[1:]

    # 3. Parse flags: --k v, --k=v, and presence-only booleans.
    flags: dict[str, Any] = {}
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("--"):
            name = tok[2:]
            if name in ("lora-modules", "lora_modules"):
                # nargs='+': consume every following value until the next --flag.
                values: list[str] = []
                if "=" in name:  # --lora-modules=... (rare) — treat RHS as one value
                    _, _, v = name.partition("=")
                    values.append(v)
                j = i + 1
                while j < len(rest) and not rest[j].startswith("--"):
                    values.append(rest[j])
                    j += 1
                flags["lora_modules"] = [_parse_lora_module(v) for v in values]
                i = j
                continue
            if "=" in name:
                k, _, v = name.partition("=")
                flags[k] = _coerce(v)
            elif i + 1 < len(rest) and not rest[i + 1].startswith("-"):
                flags[name] = _coerce(rest[i + 1])
                i += 1
            else:
                flags[name] = True
        i += 1

    if model_tag is None:
        model_tag = flags.pop("model", None)

    # 4. kebab-case -> snake_case, then peel off instance-level fields.
    norm: dict[str, Any] = {k.replace("-", "_"): v for k, v in flags.items()}

    port = norm.pop("port", None)
    host = norm.pop("host", "localhost")
    served = norm.pop("served_model_name", None)

    cuda_device: int | None = None
    if "CUDA_VISIBLE_DEVICES" in env and env["CUDA_VISIBLE_DEVICES"] != "":
        first = env["CUDA_VISIBLE_DEVICES"].split(",")[0]
        cuda_device = int(first) if first.isdigit() else None

    if model_tag is None:
        warnings.append("could not detect a model tag (expected `vllm serve <model>` or `--model`)")
    if port is None:
        warnings.append("no --port found; defaulting to 8000")
        port = 8000
    if served:
        warnings.append(f"--served-model-name ignored for naming; the router uses the group name")

    group = model_tag.split("/")[-1] if model_tag else (served or "model")
    instance_id = _slug(served) if served else f"{_slug(group)}-1"

    model_config: dict[str, Any] = {"model_tag": model_tag, **norm}

    return {
        "group": group,
        "instance": {
            "id": instance_id,
            "host": host,
            "port": port,
            "cuda_device": cuda_device,
        },
        "model_config": model_config,
        "warnings": warnings,
    }


# SGLang flag (snake_case) -> engine-neutral EngineModelConfig key. The inverse of
# launchers._SGLANG_PARAM_MAP, so a pasted SGLang command stores the same typed
# fields a vLLM one would and the launcher re-derives the SGLang flag on launch.
_SGLANG_REVERSE_MAP = {
    "context_length": "max_model_len",
    "mem_fraction_static": "gpu_memory_utilization",
    "tp_size": "tensor_parallel_size",
}


def parse_sglang_command(command: str) -> dict[str, Any]:
    """Return {group, instance, model_config, warnings} from a
    ``python -m sglang.launch_server …`` command.

    Differs from vLLM: the model is ``--model-path`` (not positional), LoRA is
    ``--lora-paths NAME=PATH …`` (not ``--lora-modules``), and the three flags
    SGLang renames are mapped back to engine-neutral keys
    (``--context-length`` → ``max_model_len`` …). The result carries
    ``model_config.engine = "sglang"``.
    """
    warnings: list[str] = []
    if not command or not command.strip():
        raise ValueError("empty command")

    tokens = shlex.split(command, comments=False)

    env: dict[str, str] = {}
    while tokens and _ENV_ASSIGN.match(tokens[0]):
        k, _, v = tokens[0].partition("=")
        env[k] = v
        tokens = tokens[1:]

    # Drop leading non-flag tokens (python, -m, sglang.launch_server, …).
    rest = tokens
    while rest and not rest[0].startswith("-"):
        rest = rest[1:]

    flags: dict[str, Any] = {}
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("--"):
            name = tok[2:]
            if name in ("lora-paths", "lora_paths"):
                # nargs='+' of NAME=PATH; consume until the next --flag.
                values: list[str] = []
                j = i + 1
                while j < len(rest) and not rest[j].startswith("--"):
                    values.append(rest[j])
                    j += 1
                flags["lora_modules"] = [_parse_lora_module(v) for v in values]
                flags["enable_lora"] = True
                i = j
                continue
            if "=" in name:
                k, _, v = name.partition("=")
                flags[k] = _coerce(v)
            elif i + 1 < len(rest) and not rest[i + 1].startswith("-"):
                flags[name] = _coerce(rest[i + 1])
                i += 1
            else:
                flags[name] = True
        i += 1

    norm: dict[str, Any] = {k.replace("-", "_"): v for k, v in flags.items()}

    # model tag: --model-path (SGLang) or --model (alias).
    model_tag = norm.pop("model_path", None) or norm.pop("model", None)
    port = norm.pop("port", None)
    host = norm.pop("host", "localhost")
    served = norm.pop("served_model_name", None)

    # Map the renamed flags back to engine-neutral keys (tp-size's long alias
    # --tensor-parallel-size already normalises to tensor_parallel_size).
    for sgl_key, neutral in _SGLANG_REVERSE_MAP.items():
        if sgl_key in norm:
            norm[neutral] = norm.pop(sgl_key)

    cuda_device: int | None = None
    if env.get("CUDA_VISIBLE_DEVICES"):
        first = env["CUDA_VISIBLE_DEVICES"].split(",")[0]
        cuda_device = int(first) if first.isdigit() else None

    if model_tag is None:
        warnings.append("could not detect a model tag (expected `--model-path <model>`)")
    if port is None:
        warnings.append("no --port found; defaulting to 30000")
        port = 30000  # SGLang's default server port
    if served:
        warnings.append("--served-model-name ignored for naming; the router uses the group name")

    group = model_tag.split("/")[-1] if model_tag else (served or "model")
    instance_id = _slug(served) if served else f"{_slug(group)}-1"

    model_config: dict[str, Any] = {"model_tag": model_tag, "engine": "sglang", **norm}

    return {
        "group": group,
        "instance": {"id": instance_id, "host": host, "port": port, "cuda_device": cuda_device},
        "model_config": model_config,
        "warnings": warnings,
    }


# llama.cpp short flags -> their long form, so the walker can normalise both. Only
# the ones the dashboard cares about; other long flags pass through as-is.
_LLAMACPP_SHORT = {
    "-m": "model", "-hf": "hf-repo", "-hfr": "hf-repo", "-hff": "hf-file",
    "-c": "ctx-size", "-ngl": "n-gpu-layers", "-b": "batch-size", "-ub": "ubatch-size",
    "-np": "parallel", "-fa": "flash-attn", "-ctk": "cache-type-k", "-ctv": "cache-type-v",
    "-sm": "split-mode", "-ts": "tensor-split", "-mg": "main-gpu", "-a": "alias",
}


def parse_llamacpp_command(command: str) -> dict[str, Any]:
    """Return {group, instance, model_config, warnings} from a ``llama-server …``
    command.

    Differs from vLLM/SGLang: the model is ``-hf <repo>[:quant]`` (HF GGUF, quant
    split into ``gguf_quant``) or ``-m <file.gguf>`` (local); the served name is
    ``--alias``; LoRA is repeatable ``--lora <gguf>`` / ``--lora-scaled <gguf>:<s>``;
    ``-c/--ctx-size`` maps to the engine-neutral ``max_model_len``; and many flags
    have short aliases. The result carries ``model_config.engine = "llamacpp"``.
    """
    warnings: list[str] = []
    if not command or not command.strip():
        raise ValueError("empty command")

    tokens = shlex.split(command, comments=False)
    env: dict[str, str] = {}
    while tokens and _ENV_ASSIGN.match(tokens[0]):
        k, _, v = tokens[0].partition("=")
        env[k] = v
        tokens = tokens[1:]
    # Drop leading non-flag tokens (llama-server, python, -m …) up to the first flag.
    rest = tokens
    while rest and not rest[0].startswith("-"):
        rest = rest[1:]

    flags: dict[str, Any] = {}
    loras: list[dict[str, Any]] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("--"):
            long = tok[2:]
        elif tok in _LLAMACPP_SHORT:
            long = _LLAMACPP_SHORT[tok]
        else:
            # Unknown short flag: skip it (+ any value) and warn, rather than emit an
            # invalid long flag the launcher would pass through verbatim.
            warnings.append(f"unrecognised flag {tok}; add it manually if needed")
            i += 2 if (i + 1 < len(rest) and not rest[i + 1].startswith("-")) else 1
            continue
        if "=" in long:  # inline --flag=value
            k, _, v = long.partition("=")
            flags[k] = _coerce(v)
            i += 1
            continue
        if long in ("lora", "lora-scaled"):
            if i + 1 < len(rest) and not rest[i + 1].startswith("-"):
                val = rest[i + 1]
                path = val.rsplit(":", 1)[0] if long == "lora-scaled" and ":" in val else val
                loras.append({"name": path.split("/")[-1].replace(".gguf", ""), "path": path})
                i += 2
                continue
        if i + 1 < len(rest) and not rest[i + 1].startswith("-"):
            flags[long] = _coerce(rest[i + 1])
            i += 2
        else:
            flags[long] = True
            i += 1

    norm: dict[str, Any] = {k.replace("-", "_"): v for k, v in flags.items()}

    # Model addressing: -hf <repo>[:quant] (split quant) or -m <file.gguf>.
    gguf_quant: str | None = None
    hf_repo = norm.pop("hf_repo", None)
    if hf_repo is not None:
        s = str(hf_repo)
        model_tag, _, quant = s.partition(":")
        gguf_quant = quant or None
    else:
        model_tag = norm.pop("model", None)
    hf_file = norm.pop("hf_file", None)
    served = norm.pop("alias", None)
    port = norm.pop("port", None)
    host = norm.pop("host", "localhost")

    # ctx-size -> engine-neutral max_model_len (inverse of launcher's mapping).
    if "ctx_size" in norm:
        norm["max_model_len"] = norm.pop("ctx_size")

    cuda_device: int | None = None
    if env.get("CUDA_VISIBLE_DEVICES"):
        first = env["CUDA_VISIBLE_DEVICES"].split(",")[0]
        cuda_device = int(first) if first.isdigit() else None

    if not model_tag:
        warnings.append("could not detect a model (expected `-hf <repo>` or `-m <file.gguf>`)")
    if port is None:
        warnings.append("no --port found; defaulting to 8080")
        port = 8080  # llama-server's default

    base = str(model_tag).split("/")[-1].replace(".gguf", "") if model_tag else "model"
    group = served or base
    instance_id = _slug(served) if served else f"{_slug(group)}-1"

    model_config: dict[str, Any] = {"model_tag": model_tag, "engine": "llamacpp", **norm}
    if gguf_quant:
        model_config["gguf_quant"] = gguf_quant
    if hf_file:
        model_config["hf_file"] = hf_file
    if served:
        model_config["served_model_name"] = served
    if loras:
        model_config["lora_modules"] = loras
        model_config["enable_lora"] = True

    return {
        "group": group,
        "instance": {"id": instance_id, "host": host, "port": port, "cuda_device": cuda_device},
        "model_config": model_config,
        "warnings": warnings,
    }


def parse_command(command: str, engine: str | None = None) -> dict[str, Any]:
    """Engine-aware entry point for the dashboard's paste-a-command flow.

    ``engine`` ('vllm' | 'sglang' | 'llamacpp') forces the parser; when omitted it
    is sniffed from the command (``llama-server`` → llamacpp, ``sglang.launch_server``
    → sglang, else vLLM).
    """
    eng = (engine or "").strip().lower()
    if not eng:
        cl = command.lower()
        eng = "llamacpp" if "llama-server" in cl else "sglang" if "sglang" in cl else "vllm"
    if eng == "llamacpp":
        return parse_llamacpp_command(command)
    if eng == "sglang":
        return parse_sglang_command(command)
    return parse_vllm_command(command)

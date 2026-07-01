"""LoRA adapter library: inventory + management of a local adapter directory.

Adapters live one-folder-each under LLMOPS_LORA_DIR (bind-mounted to /lora in
docker; ~/.cache/lora_adapters locally). A folder is an adapter when it holds a
PEFT `adapter_config.json` — which also tells us the base model + rank, so the
dashboard can warn on base mismatch and auto-fill max_lora_rank.

The path each adapter reports is exactly what goes into a model's
`model_config.lora_modules[].path`, so the backend-spawned vLLM mounts it.

All functions are synchronous (disk/network IO) — callers use a thread executor.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any, Optional

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def lora_root() -> str:
    return os.environ.get("LLMOPS_LORA_DIR") or os.path.expanduser("~/.cache/lora_adapters")


def adapter_dir(name: str) -> str:
    """Resolved folder for an adapter name, guarded against path traversal."""
    # `.` and `..` match _SAFE_NAME (dot is allowed) but must be rejected.
    if not name or name in (".", "..") or not _SAFE_NAME.match(name):
        raise ValueError(f"invalid adapter name: {name!r}")
    return os.path.join(lora_root(), name)


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _read_adapter_config(folder: str) -> Optional[dict]:
    cfg_path = os.path.join(folder, "adapter_config.json")
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}  # present but unreadable — still a (broken) adapter folder


def scan() -> list[dict[str, Any]]:
    """Adapters under the LoRA root.

    Two shapes coexist:
      - a **folder** with a PEFT ``adapter_config.json`` (HF/safetensors LoRA for
        vLLM/SGLang) — base model + rank are parsed from the config;
      - a loose **``*.gguf``** file (a GGUF LoRA for llama.cpp) — llama.cpp loads it by
        path via ``--lora``. GGUF metadata isn't cheaply readable here, so base/rank
        are left null; the ``format`` field lets the dashboard route it to the right
        engine picker.
    """
    root = lora_root()
    out: list[dict[str, Any]] = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        entry = os.path.join(root, name)
        if os.path.isdir(entry):
            cfg = _read_adapter_config(entry)
            if cfg is None:
                continue  # not an adapter (no adapter_config.json)
            out.append({
                "name": name,
                "path": entry,
                "base_model": cfg.get("base_model_name_or_path"),
                "rank": cfg.get("r"),
                "alpha": cfg.get("lora_alpha"),
                "target_modules": list(cfg.get("target_modules") or []),
                "size_on_disk": dir_size(entry),
                "format": "peft",
            })
        elif os.path.isfile(entry) and name.lower().endswith(".gguf"):
            out.append({
                "name": name,
                "path": entry,
                "base_model": None,  # not cheaply readable from GGUF metadata here
                "rank": None,
                "alpha": None,
                "target_modules": [],
                "size_on_disk": os.path.getsize(entry),
                "format": "gguf",
            })
    return out


def disk_usage() -> dict[str, int]:
    """total/used/free bytes of the volume holding the LoRA root."""
    root = lora_root()
    probe = root if os.path.exists(root) else os.path.dirname(root) or "/"
    usage = shutil.disk_usage(probe)
    return {"total": usage.total, "used": usage.used, "free": usage.free}


def delete(name: str) -> bool:
    """Remove an adapter (a PEFT folder or a loose .gguf file). False if absent."""
    target = adapter_dir(name)
    if os.path.isdir(target):
        shutil.rmtree(target)
        return True
    if os.path.isfile(target):
        os.remove(target)
        return True
    return False


def download(repo_id: str, name: str, token: Optional[str] = None) -> str:
    """Blocking snapshot download of an adapter repo into <root>/<name>."""
    from huggingface_hub import snapshot_download

    target = adapter_dir(name)
    snapshot_download(repo_id, local_dir=target, token=token)
    return target

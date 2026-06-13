"""Dynamic-model overlay: user-added models persisted separately from the
hand-maintained config.yaml, then merged on top of it at load time.

The overlay file is JSON shaped like the config's LLM_engines block:

    {"LLM_engines": {"<group>": {"instances": [ {...} ], "model_config": {...}}}}

merge_into() layers it onto a raw config dict (a new group is added whole; an
extra instance for an existing group is appended), and build_merged_config()
returns the validated RootConfig the registry/manager run on.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

import yaml

from app.core.config import get_config_path
from schema import RootConfig

OVERLAY_ENV = "LLMOPS_OVERLAY_PATH"
# app/services/overlay.py -> repo root is 4 dirs up.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")
)
_DEFAULT_OVERLAY = os.path.join(_REPO_ROOT, "data", "dynamic_models.json")


def overlay_path() -> str:
    return os.environ.get(OVERLAY_ENV, _DEFAULT_OVERLAY)


def load_overlay(path: str | None = None) -> dict[str, Any]:
    p = path or overlay_path()
    if not os.path.exists(p):
        return {"LLM_engines": {}}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"LLM_engines": {}}
    data.setdefault("LLM_engines", {})
    return data


def save_overlay(overlay: dict[str, Any], path: str | None = None) -> None:
    p = path or overlay_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(overlay, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)  # atomic on POSIX


def merge_into(base_raw: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Layer overlay LLM_engines onto a raw config dict (returns a new dict)."""
    merged = copy.deepcopy(base_raw)
    engines = merged.setdefault("LLM_engines", {})
    for group, entry in overlay.get("LLM_engines", {}).items():
        if group in engines:
            existing_ids = {i.get("id") for i in engines[group].get("instances", [])}
            for inst in entry.get("instances", []):
                if inst.get("id") not in existing_ids:
                    engines[group].setdefault("instances", []).append(inst)
        else:
            engines[group] = copy.deepcopy(entry)
    return merged


def build_merged_config(config_path: str | None = None, overlay: dict | None = None) -> RootConfig:
    """Load config.yaml, merge the overlay, return a validated RootConfig."""
    path = config_path or get_config_path()
    with open(path, encoding="utf-8") as f:
        base_raw = yaml.safe_load(f) or {}
    ov = overlay if overlay is not None else load_overlay()
    return RootConfig.model_validate(merge_into(base_raw, ov))


def overlay_owns(overlay: dict[str, Any], group: str, instance_id: str) -> bool:
    entry = overlay.get("LLM_engines", {}).get(group)
    if not entry:
        return False
    return any(i.get("id") == instance_id for i in entry.get("instances", []))

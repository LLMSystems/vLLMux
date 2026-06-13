"""Dynamic-model overlay for the router.

Mirrors the dashboard backend: user-added models live in a separate JSON file
(data/dynamic_models.json) shaped like the config's LLM_engines block, and are
merged on top of the validated config.yaml so the router can route to them too.

Kept dict-based (like config_loader) so routing/metrics code is unchanged.
"""
import copy
import json
import os

from src.llm_router.config_loader import _REPO_ROOT, load_config

OVERLAY_ENV = "LLMOPS_OVERLAY_PATH"
_DEFAULT_OVERLAY = os.path.join(_REPO_ROOT, "data", "dynamic_models.json")


def overlay_path() -> str:
    return os.environ.get(OVERLAY_ENV, _DEFAULT_OVERLAY)


def load_overlay(path: str | None = None) -> dict:
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


def merge_into(base: dict, overlay: dict) -> dict:
    """Layer overlay LLM_engines onto a config dict (new dict; base untouched)."""
    merged = copy.deepcopy(base)
    engines = merged.setdefault("LLM_engines", {})
    for group, entry in overlay.get("LLM_engines", {}).items():
        if group in engines:
            existing = {i.get("id") for i in engines[group].get("instances", [])}
            for inst in entry.get("instances", []):
                if inst.get("id") not in existing:
                    engines[group].setdefault("instances", []).append(inst)
        else:
            engines[group] = copy.deepcopy(entry)
    return merged


def load_config_with_overlay(config_path: str) -> dict:
    """Base config.yaml (validated) + dynamic-model overlay, as a plain dict."""
    return merge_into(load_config(config_path), load_overlay())

"""LoRA-aware model resolution for the router.

A LoRA adapter is served by vLLM under its own *served name* (the `name` in the
group's `model_config.lora_modules`), distinct from the base group key. Clients
put that served name in the request `model` field.

Two things the rest of the router needs:
  - resolve_model(): map a requested `model` to the group it routes through and
    the exact name to forward upstream. For a base group we rewrite to the
    model_tag (historical behaviour); for a LoRA we keep the served name so vLLM
    picks the adapter, but still route over the *base group's* instances/metrics.
  - iter_models(): the flat list for GET /v1/models, base groups + their LoRAs
    (LoRAs carry a `parent` pointing at the base group).
"""
from __future__ import annotations

from typing import Any, Optional


def _lora_modules(engine: dict) -> list[dict]:
    mods = (engine.get("model_config") or {}).get("lora_modules") or []
    return [m for m in mods if isinstance(m, dict) and m.get("name")]


def resolve_model(config: dict, requested: str) -> Optional[dict]:
    """Resolve a requested model name.

    Returns a dict with:
      - route_key:    group key used for instance selection + metrics + logging
      - model_cfg:    that group's config entry (instances + model_config)
      - forward_name: the value to put in the upstream request `model` field
      - is_lora:      True when `requested` matched a LoRA served name
    or None when nothing matches.
    """
    engines = config.get("LLM_engines", {})
    engine = engines.get(requested)
    if engine is not None:
        model_tag = (engine.get("model_config") or {}).get("model_tag", requested)
        return {
            "route_key": requested,
            "model_cfg": engine,
            "forward_name": model_tag,
            "is_lora": False,
        }
    for group, eng in engines.items():
        for mod in _lora_modules(eng):
            if mod["name"] == requested:
                return {
                    "route_key": group,
                    "model_cfg": eng,
                    "forward_name": requested,  # keep the served LoRA name
                    "is_lora": True,
                }
    return None


def iter_models(config: dict) -> list[dict[str, Any]]:
    """Flat /v1/models payload: every base group, plus each LoRA with a parent."""
    out: list[dict[str, Any]] = []
    for group, eng in config.get("LLM_engines", {}).items():
        out.append({"id": group, "object": "model"})
        for mod in _lora_modules(eng):
            out.append({"id": mod["name"], "object": "model", "parent": group})
    return out

"""Flatten a validated RootConfig into the summary the dashboard renders.

Pure function over an already-loaded RootConfig — no file IO here (loading lives
in app.core.config), so it stays trivially unit-testable.
"""
from __future__ import annotations

from typing import Any

from schema import RootConfig


def _extra(model) -> dict:
    """pydantic v2 stores unknown (extra='allow') keys on .model_extra."""
    return getattr(model, "model_extra", None) or {}


def summarize_config(config: RootConfig) -> dict[str, Any]:
    llm_engines: dict[str, Any] = {}

    for name, engine in config.LLM_engines.items():
        settings = engine.settings
        settings_extra = _extra(settings)
        for inst in engine.instances:
            inst_extra = _extra(inst)
            key = f"{name}::{inst.id}"
            llm_engines[key] = {
                "port": inst.port,
                "cuda_device": inst.cuda_device,
                "max_model_len": settings.max_model_len,
                "gpu_memory_utilization": settings.gpu_memory_utilization,
                "tool_parser": (
                    settings_extra.get("tool-call-parser")
                    or inst_extra.get("reasoning_parser")
                    or settings_extra.get("reasoning_parser")
                    or "unknown"
                ),
            }

    emb = config.embedding_server
    embedding_summary = {
        "port": emb.port if emb else None,
        "cuda_device": emb.cuda_device if emb else None,
        "embedding_models": list(emb.embedding_models.keys()) if emb else [],
        "reranking_models": list(emb.reranking_models.keys()) if emb else [],
    }

    return {
        "server": config.server.model_dump(),
        "LLM_engines": llm_engines,
        "embedding_server": embedding_summary,
    }

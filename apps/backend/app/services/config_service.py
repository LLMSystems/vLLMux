"""Flatten a validated RootConfig into the summary the dashboard renders.

Pure function over an already-loaded RootConfig — no file IO here (loading lives
in app.core.config), so it stays trivially unit-testable.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # `schema` lives in packages/config-schema and is only put on sys.path by
    # app.core.config at runtime. It's used here purely as a type hint (and this
    # module has `from __future__ import annotations`), so keep the import
    # type-only to avoid depending on import order.
    from schema import RootConfig


def _extra(model) -> dict:
    """pydantic v2 stores unknown (extra='allow') keys on .model_extra."""
    return getattr(model, "model_extra", None) or {}


def summarize_config(config: RootConfig) -> dict[str, Any]:
    llm_engines: dict[str, Any] = {}

    for name, engine in config.LLM_engines.items():
        settings = engine.settings
        settings_extra = _extra(settings)
        # Full vLLM model_config (declared fields + any extra='allow' flags) so
        # the frontend can render every parameter generically. Per-instance extras
        # override the shared group settings.
        settings_full = settings.model_dump()

        for inst in engine.instances:
            inst_extra = _extra(inst)
            key = f"{name}::{inst.id}"
            llm_engines[key] = {
                "host": inst.host,
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
                # Complete model_config for this instance (shared settings + overrides).
                "settings": {**settings_full, **inst_extra},
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

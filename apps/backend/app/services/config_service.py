"""Turn the raw config.yaml into the flattened summary the dashboard renders.

Pure function over an already-loaded config dict — no file IO here, so it is
trivially unit-testable. The *loading* lives in app.core.config.
"""
from typing import Any


def summarize_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    llm_engines: dict[str, Any] = {}

    for name, cfg in raw_config.get("LLM_engines", {}).items():
        instances = cfg.get("instances", [])
        shared_model_cfg = cfg.get("model_config", {})
        if not instances:
            continue
        for instance in instances:
            instance_id = instance.get("id")
            if not instance_id:
                continue
            key = f"{name}::{instance_id}"
            llm_engines[key] = {
                "port": instance.get("port", cfg.get("port")),
                "cuda_device": instance.get("cuda_device", cfg.get("cuda_device")),
                "max_model_len": shared_model_cfg.get(
                    "max_model_len", cfg.get("max_model_len")
                ),
                "gpu_memory_utilization": shared_model_cfg.get(
                    "gpu_memory_utilization", cfg.get("gpu_memory_utilization")
                ),
                "tool_parser": (
                    shared_model_cfg.get("tool-call-parser")
                    or instance.get("reasoning_parser")
                    or cfg.get("tool-call-parser")
                    or cfg.get("reasoning_parser")
                    or "unknown"
                ),
            }

    embedding_server = raw_config.get("embedding_server", {})
    embedding_summary = {
        "port": embedding_server.get("port"),
        "cuda_device": embedding_server.get("cuda_device"),
        "embedding_models": list(embedding_server.get("embedding_models").keys())
        if embedding_server.get("embedding_models")
        else [],
        "reranking_models": list(embedding_server.get("reranking_models").keys())
        if embedding_server.get("reranking_models")
        else [],
    }

    return {
        "server": raw_config.get("server", {}),
        "LLM_engines": llm_engines,
        "embedding_server": embedding_summary,
    }

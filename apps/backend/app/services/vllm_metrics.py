"""Parse vLLM's startup capacity/memory/compile metrics out of an engine log.

These are printed once near the top of the log when a model finishes loading and
never change afterwards, so we read only the log head and regex them out. Every
field is optional: missing lines (or a different vLLM version) yield None rather
than an error, so parsing can never break the panel.
"""
from __future__ import annotations

import re
from typing import Optional


def _num(pattern: str, text: str, group: int = 1) -> Optional[float]:
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return float(m.group(group).replace(",", ""))
    except (ValueError, IndexError):
        return None


def parse_startup_metrics(log: str) -> dict:
    """Structured startup metrics. Sections present even if all-None; the UI shows
    only the fields it actually finds."""
    kv_tokens = _num(r"GPU KV cache size:\s*([\d,]+)\s*tokens", log)
    conc_req_tokens = _num(r"Maximum concurrency for ([\d,]+) tokens per request", log)
    max_concurrency = _num(r"Maximum concurrency for [\d,]+ tokens per request:\s*([\d.]+)x", log)
    kv_mem = _num(r"Available KV cache memory:\s*([\d.]+)\s*GiB", log)

    capacity = {
        "kv_cache_tokens": int(kv_tokens) if kv_tokens is not None else None,
        "max_concurrency": max_concurrency,
        "concurrency_req_tokens": int(conc_req_tokens) if conc_req_tokens is not None else None,
        "kv_cache_gib": kv_mem,
    }
    memory = {
        "model_gib": _num(r"Model loading took ([\d.]+) GiB", log),
        "cudagraph_gib": _num(r"Estimated CUDA graph memory:\s*([\d.]+)\s*GiB", log),
        "kv_cache_gib": kv_mem,
    }
    startup = {
        "weights_load_s": _num(r"Loading weights took ([\d.]+) seconds", log),
        "model_load_s": _num(r"Model loading took [\d.]+ GiB memory and ([\d.]+) seconds", log),
        "compile_s": _num(r"torch\.compile took ([\d.]+) s", log),
        "warmup_s": _num(r"warmup run took ([\d.]+) s", log),
    }
    # Precise decimal (\d+(?:\.\d+)?) so a sentence-final period isn't swallowed
    # into the captured number (e.g. "...to 0.8374.").
    gmu = {
        "current": _num(r"current --gpu-memory-utilization=(\d+(?:\.\d+)?)", log),
        "effective": _num(r"equivalent to --gpu-memory-utilization=(\d+(?:\.\d+)?)", log),
        "suggested": _num(r"increase --gpu-memory-utilization to (\d+(?:\.\d+)?)", log),
    }
    has_any = any(v is not None for sect in (capacity, memory, startup, gmu) for v in sect.values())
    return {
        "has_any": has_any,
        "capacity": capacity,
        "memory": memory,
        "startup": startup,
        "gpu_mem_util": gmu,
    }

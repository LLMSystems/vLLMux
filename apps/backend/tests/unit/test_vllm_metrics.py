"""Parsing vLLM startup metrics out of an engine log head."""
import pytest

from app.services.vllm_metrics import parse_startup_metrics

pytestmark = pytest.mark.unit

SAMPLE = """\
(EngineCore pid=1501) INFO [default_loader.py:397] Loading weights took 0.38 seconds
(EngineCore pid=1501) INFO [gpu_model_runner.py:5187] Model loading took 1.19 GiB memory and 1.919768 seconds
(EngineCore pid=1501) INFO [monitor.py:53] torch.compile took 22.16 s in total
(EngineCore pid=1501) INFO [monitor.py:81] Initial profiling/warmup run took 0.94 s
(EngineCore pid=1501) INFO [gpu_model_runner.py:6517] Estimated CUDA graph memory: 0.94 GiB total
(EngineCore pid=1501) INFO [gpu_worker.py:480] Available KV cache memory: 3.24 GiB
(EngineCore pid=1501) INFO [gpu_worker.py:495] The current --gpu-memory-utilization=0.7200 is equivalent to --gpu-memory-utilization=0.6026 without CUDA graph memory profiling. To maintain the same effective KV cache size as before, increase --gpu-memory-utilization to 0.8374.
(EngineCore pid=1501) INFO [kv_cache_utils.py:1744] GPU KV cache size: 60,623 tokens
(EngineCore pid=1501) INFO [kv_cache_utils.py:1745] Maximum concurrency for 5,000 tokens per request: 12.12x
"""


def test_parse_full_sample():
    m = parse_startup_metrics(SAMPLE)
    assert m["has_any"] is True
    assert m["capacity"] == {
        "kv_cache_tokens": 60623, "max_concurrency": 12.12,
        "concurrency_req_tokens": 5000, "kv_cache_gib": 3.24,
    }
    assert m["memory"] == {"model_gib": 1.19, "cudagraph_gib": 0.94, "kv_cache_gib": 3.24}
    assert m["startup"] == {
        "weights_load_s": 0.38, "model_load_s": 1.919768, "compile_s": 22.16, "warmup_s": 0.94,
    }
    # sentence-final period must not be swallowed into the suggested value
    assert m["gpu_mem_util"] == {"current": 0.72, "effective": 0.6026, "suggested": 0.8374}


def test_parse_partial_is_null_safe():
    m = parse_startup_metrics("(EngineCore) GPU KV cache size: 1,024 tokens")
    assert m["has_any"] is True
    assert m["capacity"]["kv_cache_tokens"] == 1024
    assert m["capacity"]["max_concurrency"] is None
    assert m["startup"]["compile_s"] is None


def test_parse_empty_has_nothing():
    m = parse_startup_metrics("nothing relevant here\n")
    assert m["has_any"] is False

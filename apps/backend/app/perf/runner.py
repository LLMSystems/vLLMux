"""Standalone evalscope perf runner — invoked as a subprocess by PerfManager.

Usage:
    python -m app.perf.runner <config.json> <run_dir>

Reads an Arguments config (JSON), runs the benchmark, and writes a serialized
summary to ``<run_dir>/result.json``. Imports only evalscope so the heavy perf
dependency and its own asyncio loop stay out of the backend's process.
"""
import json
import os
import sys


def _dump(obj):
    """Serialize a pydantic BenchmarkSummary / PercentileResult (or pass through)."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            return str(obj)
    return obj


def main() -> int:
    config_path, run_dir = sys.argv[1], sys.argv[2]
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    from evalscope.perf.arguments import Arguments
    from evalscope.perf.main import run_perf_benchmark

    results = run_perf_benchmark(Arguments(**cfg))

    serialized = {}
    for key, val in (results or {}).items():
        serialized[key] = {
            "metrics": _dump(val.get("metrics")),
            "percentiles": _dump(val.get("percentiles")),
        }
    out_path = os.path.join(run_dir, "result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(serialized, f, ensure_ascii=False, default=str)
    print(f"[runner] wrote {out_path} with {len(serialized)} point(s)", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)

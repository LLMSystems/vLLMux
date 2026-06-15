"""Standalone evalscope perf runner — invoked as a subprocess by PerfManager.

Usage:
    python -m app.perf.runner <config.json> <run_dir>

Reads an Arguments config (JSON), runs the benchmark, and writes a normalized
summary to ``<run_dir>/result.json``:

    {"points": {"<key>": {"metrics": {...}, "percentiles": {"rows": [...]}}},
     "sla":    [ {criteria, variable, max_satisfied, note, points:[...]} ] | null }

Sweep returns pydantic summaries; SLA returns aliased dicts — both are coerced
to the same canonical shape here so the backend parser stays mode-agnostic.
Imports only evalscope so its heavy deps / asyncio loop stay out of the backend.
"""
import json
import os
import sys


def _norm_metrics(m):
    if m is None:
        return None
    if hasattr(m, "model_dump"):  # sweep: BenchmarkSummary object
        return m.model_dump()
    from evalscope.perf.utils.perf_models import BenchmarkSummary  # SLA: aliased dict
    return BenchmarkSummary.from_dict(m).model_dump()


def _norm_pct(p):
    if p is None:
        return None
    if hasattr(p, "model_dump"):  # sweep: PercentileResult object
        return p.model_dump()
    from evalscope.perf.utils.perf_models import PercentileResult  # SLA: transposed dict
    return PercentileResult.from_transposed(p).model_dump()


def _val_from_key(key: str):
    try:
        return int(key.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None


def _compute_sla(cfg: dict, results: dict) -> list:
    """Per-group SLA verdict using evalscope's own criterion + metric extraction
    (so units / pass-fail match evalscope exactly). Groups are OR-ed: each gets
    its own binary-search answer (max value satisfying the group)."""
    from evalscope.perf.sla.sla_criterion import SLAMax, SLAMin
    from evalscope.perf.sla.sla_run import check_sla, get_metric_values, parse_sla_params

    variable = cfg.get("sla_variable", "parallel")
    groups = parse_sla_params(cfg["sla_params"])
    items = sorted(
        ((_val_from_key(k), r) for k, r in results.items()),
        key=lambda x: (x[0] if x[0] is not None else 0),
    )

    out = []
    for group in groups:
        criteria = " AND ".join(f"{m} {c.format_cond('')}" for m, c in group.items())
        is_opt = any(isinstance(c, (SLAMax, SLAMin)) for c in group.values())
        pts = []
        for val, res in items:
            vals = get_metric_values(res)
            pts.append({
                "val": val,
                "passed": bool(check_sla(res, [group])),
                "rps": vals.get("rps"),
                "tps": vals.get("tps"),
                "metrics": {m: vals.get(m) for m in group},
            })
        if is_opt:
            metric = next(iter(group))
            mode = "max" if isinstance(group[metric], SLAMax) else "min"
            valid = [(p["val"], p["metrics"][metric]) for p in pts if p["metrics"][metric] is not None]
            if valid:
                best = (max if mode == "max" else min)(valid, key=lambda x: x[1])
                ms, note = best[0], f"Best {metric}: {best[1]:.4f}"
            else:
                ms, note = None, "No data"
        else:
            passing = [p["val"] for p in pts if p["passed"] and p["val"] is not None]
            ms = max(passing) if passing else None
            note = "Satisfied" if ms is not None else "Failed"
        out.append({"criteria": criteria, "variable": variable, "max_satisfied": ms, "note": note, "points": pts})
    return out


def main() -> int:
    config_path, run_dir = sys.argv[1], sys.argv[2]
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    from evalscope.perf.arguments import Arguments
    from evalscope.perf.main import run_perf_benchmark

    results = run_perf_benchmark(Arguments(**cfg))

    points = {
        key: {"metrics": _norm_metrics(val.get("metrics")), "percentiles": _norm_pct(val.get("percentiles"))}
        for key, val in (results or {}).items()
    }
    sla = _compute_sla(cfg, results) if cfg.get("sla_auto_tune") else None

    out_path = os.path.join(run_dir, "result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"points": points, "sla": sla}, f, ensure_ascii=False, default=str)
    print(f"[runner] wrote {out_path}: {len(points)} point(s), sla={'yes' if sla else 'no'}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)

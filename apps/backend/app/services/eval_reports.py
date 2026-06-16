"""On-demand parsing of an eval run's rich evalscope output.

The slim per-run `result.json` (scores only) rides in the run list and stays
cheap. The heavier detail — per-dataset speed/throughput, per-subset breakdown,
and per-sample predictions/reviews — is parsed lazily from the run directory by
these helpers and served through dedicated endpoints, so the browser never has
to load (or render) thousands of raw samples at once.

Layout under <run_dir>:
  reports/<model>/<dataset>.json          # scores + categories/subsets + perf_metrics
  predictions/<model>/<dataset>_<sub>.jsonl  # per-sample prompt + model answer + perf
  reviews/<model>/<dataset>_<sub>.jsonl      # per-sample target + sample_score
"""
from __future__ import annotations

import functools
import glob
import json
import os
from typing import Optional


def _truncate(s, limit: int = 240) -> Optional[str]:
    if s is None:
        return None
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    return s if len(s) <= limit else s[:limit] + "…"


def _as_text(v) -> str:
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


# ---- report: per-dataset summary (scores + subsets + speed + description) ----

def _projected_perf(pm: Optional[dict]) -> Optional[dict]:
    """Compact projection of evalscope perf_metrics.summary — just the headline
    numbers the UI shows, not the full distribution."""
    if not pm:
        return None
    s = pm.get("summary") or {}
    lat = s.get("latency") or {}
    usage = s.get("usage") or {}
    tput = s.get("throughput") or {}
    return {
        "n_samples": s.get("n_samples"),
        "latency": {
            "mean": lat.get("mean"), "p50": lat.get("50%"),
            "p99": lat.get("99%"), "max": lat.get("max"),
        },
        "output_tps": tput.get("avg_output_tps"),
        "req_ps": tput.get("avg_req_ps"),
        "input_tokens_mean": (usage.get("input_tokens") or {}).get("mean"),
        "output_tokens_mean": (usage.get("output_tokens") or {}).get("mean"),
    }


def _projected_metric(m: dict) -> dict:
    # Flatten categories -> subsets, dropping aggregate rows (the macro total is
    # already on the metric). Only meaningful for multi-subject datasets.
    subsets = [
        {"name": s.get("name"), "score": s.get("score"), "num": s.get("num")}
        for c in (m.get("categories") or [])
        for s in (c.get("subsets") or [])
        if not s.get("is_aggregate")
    ]
    return {
        "name": m.get("name"), "score": m.get("score"),
        "num": m.get("num"), "subsets": subsets,
    }


def report(run_dir: str) -> list[dict]:
    """Per-dataset rich summary for a run. Empty if reports aren't written yet."""
    out: list[dict] = []
    for path in sorted(glob.glob(os.path.join(run_dir, "reports", "*", "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                rep = json.load(f)
        except (OSError, ValueError):
            continue
        out.append({
            "dataset": rep.get("dataset_name"),
            "pretty": rep.get("dataset_pretty_name") or rep.get("dataset_name"),
            "description": rep.get("dataset_description"),
            "score": rep.get("score"),
            "num": rep.get("num"),
            "metrics": [_projected_metric(m) for m in (rep.get("metrics") or [])],
            "perf": _projected_perf(rep.get("perf_metrics")),
        })
    return out


# ---- samples: per-sample rows (compact list) + single full detail ------------

def _review_files(run_dir: str, dataset: str) -> list[str]:
    return sorted(glob.glob(os.path.join(run_dir, "reviews", "*", f"{dataset}_*.jsonl")) +
                  glob.glob(os.path.join(run_dir, "reviews", "*", f"{dataset}.jsonl")))


def _pred_files(run_dir: str, dataset: str) -> list[str]:
    return sorted(glob.glob(os.path.join(run_dir, "predictions", "*", f"{dataset}_*.jsonl")) +
                  glob.glob(os.path.join(run_dir, "predictions", "*", f"{dataset}.jsonl")))


def _score_of(review: dict) -> tuple[dict, Optional[float], bool]:
    """Return (per-metric value dict, representative mean, correct bool)."""
    sc = (review.get("sample_score") or {}).get("score") or {}
    value = sc.get("value") or {}
    nums = [v for v in value.values() if isinstance(v, (int, float))]
    mean = (sum(nums) / len(nums)) if nums else None
    return value, mean, (mean is not None and mean >= 0.5)


@functools.lru_cache(maxsize=16)
def _compact_rows(run_dir: str, dataset: str) -> tuple:
    """All review rows for a dataset, projected to compact dicts. Cached because a
    finished run's files are immutable and pagination re-reads them per request."""
    rows: list[dict] = []
    for path in _review_files(run_dir, dataset):
        subset = os.path.basename(path)[: -len(".jsonl")]
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    sc = (r.get("sample_score") or {}).get("score") or {}
                    value, mean, correct = _score_of(r)
                    rows.append({
                        "index": r.get("index"),
                        "subset": subset,
                        "scores": value,
                        "score": mean,
                        "correct": correct,
                        "extracted": _truncate(sc.get("extracted_prediction")),
                        "target": _truncate(_as_text(r.get("target"))),
                        "preview": _truncate(sc.get("prediction")),
                    })
        except (OSError, ValueError):
            continue
    return tuple(rows)


def samples(run_dir: str, dataset: str, filt: str = "all",
            page: int = 1, page_size: int = 50) -> dict:
    """Filtered + paginated compact sample rows. `filt`: all|correct|wrong."""
    rows = list(_compact_rows(run_dir, dataset))
    if filt == "correct":
        rows = [r for r in rows if r["correct"]]
    elif filt == "wrong":
        rows = [r for r in rows if not r["correct"]]
    total = len(rows)
    correct = sum(1 for r in _compact_rows(run_dir, dataset) if r["correct"])
    page = max(1, page)
    start = (page - 1) * page_size
    return {
        "total": total,
        "total_all": len(_compact_rows(run_dir, dataset)),
        "total_correct": correct,
        "page": page,
        "page_size": page_size,
        "samples": rows[start:start + page_size],
    }


def _find_row(files: list[str], index: int) -> Optional[dict]:
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if r.get("index") == index:
                        return r
        except (OSError, ValueError):
            continue
    return None


def _answer_and_perf(pred: Optional[dict]) -> tuple[Optional[str], Optional[dict]]:
    if not pred:
        return None, None
    try:
        msg = pred["model_output"]["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None, None
    pm = msg.get("perf_metrics") or {}
    perf = {
        "latency": pm.get("latency"), "ttft": pm.get("ttft"),
        "input_tokens": pm.get("input_tokens"), "output_tokens": pm.get("output_tokens"),
    } if pm else None
    return msg.get("content"), perf


def sample(run_dir: str, dataset: str, index: int) -> Optional[dict]:
    """Full detail for one sample: prompt, model answer, target, scores, perf."""
    review = _find_row(_review_files(run_dir, dataset), index)
    pred = _find_row(_pred_files(run_dir, dataset), index)
    if review is None and pred is None:
        return None
    review = review or {}
    answer, perf = _answer_and_perf(pred)
    value, mean, correct = _score_of(review)
    prompt = (pred or {}).get("messages") or review.get("messages") or []
    return {
        "index": index,
        "prompt": prompt,
        "answer": answer,
        "target": _as_text(review.get("target")) if review.get("target") is not None else None,
        "scores": value,
        "correct": correct,
        "perf": perf,
    }

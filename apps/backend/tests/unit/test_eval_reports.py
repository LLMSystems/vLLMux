"""On-demand parsing of an eval run's rich evalscope output."""
import json
import os

import pytest

from app.services import eval_reports

pytestmark = pytest.mark.unit


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _run(tmp_path):
    rd = str(tmp_path)
    # report json
    report = {
        "dataset_name": "gsm8k", "dataset_pretty_name": "GSM8K",
        "dataset_description": "Grade-school math.", "score": 0.5, "num": 4,
        "metrics": [{
            "name": "mean_acc", "score": 0.5, "num": 4,
            "categories": [{"subsets": [
                {"name": "main", "score": 0.5, "num": 4, "is_aggregate": False},
                {"name": "ALL", "score": 0.5, "num": 4, "is_aggregate": True},
            ]}],
        }],
        "perf_metrics": {"summary": {
            "n_samples": 4,
            "latency": {"mean": 10.0, "50%": 9.0, "99%": 20.0, "max": 21.0},
            "throughput": {"avg_output_tps": 40.0, "avg_req_ps": 0.1},
            "usage": {"input_tokens": {"mean": 600.0}, "output_tokens": {"mean": 300.0}},
        }},
    }
    os.makedirs(os.path.join(rd, "reports", "m"), exist_ok=True)
    with open(os.path.join(rd, "reports", "m", "gsm8k.json"), "w", encoding="utf-8") as f:
        json.dump(report, f)
    # reviews: 2 correct, 2 wrong
    _write_jsonl(os.path.join(rd, "reviews", "m", "gsm8k_main.jsonl"), [
        {"index": i, "target": str(i), "messages": [{"role": "user", "content": f"q{i}"}],
         "sample_score": {"score": {
             "value": {"acc": 1.0 if i % 2 else 0.0},
             "extracted_prediction": str(i), "prediction": f"answer {i}"}}}
        for i in range(1, 5)
    ])
    # predictions: answer + per-sample perf
    _write_jsonl(os.path.join(rd, "predictions", "m", "gsm8k_main.jsonl"), [
        {"index": i, "messages": [{"role": "user", "content": f"q{i}"}],
         "model_output": {"choices": [{"message": {
             "content": f"full answer {i}",
             "perf_metrics": {"latency": 1.0 * i, "ttft": 0.5, "input_tokens": 600, "output_tokens": 300}}}]}}
        for i in range(1, 5)
    ])
    return rd


def test_report_projects_score_perf_and_subsets(tmp_path):
    rd = _run(tmp_path)
    [d] = eval_reports.report(rd)
    assert d["dataset"] == "gsm8k" and d["pretty"] == "GSM8K" and d["score"] == 0.5
    assert d["description"] == "Grade-school math."
    assert d["perf"]["latency"] == {"mean": 10.0, "p50": 9.0, "p99": 20.0, "max": 21.0}
    assert d["perf"]["output_tps"] == 40.0 and d["perf"]["output_tokens_mean"] == 300.0
    # aggregate subset dropped, real one kept
    subs = d["metrics"][0]["subsets"]
    assert subs == [{"name": "main", "score": 0.5, "num": 4}]


def test_report_empty_when_no_reports(tmp_path):
    assert eval_reports.report(str(tmp_path)) == []


def test_samples_filter_and_paginate(tmp_path):
    rd = _run(tmp_path)
    eval_reports._compact_rows.cache_clear()
    allp = eval_reports.samples(rd, "gsm8k", "all", 1, 50)
    assert allp["total"] == 4 and allp["total_correct"] == 2
    wrong = eval_reports.samples(rd, "gsm8k", "wrong", 1, 50)
    assert wrong["total"] == 2 and all(not r["correct"] for r in wrong["samples"])
    # pagination
    p1 = eval_reports.samples(rd, "gsm8k", "all", 1, 2)
    p2 = eval_reports.samples(rd, "gsm8k", "all", 2, 2)
    assert len(p1["samples"]) == 2 and len(p2["samples"]) == 2
    assert {r["index"] for r in p1["samples"]} != {r["index"] for r in p2["samples"]}
    # compact rows carry projection, not full text
    row = allp["samples"][0]
    assert {"index", "correct", "score", "extracted", "target", "preview"} <= row.keys()


def test_sample_full_detail(tmp_path):
    rd = _run(tmp_path)
    eval_reports._compact_rows.cache_clear()
    s = eval_reports.sample(rd, "gsm8k", 1)
    assert s["index"] == 1 and s["correct"] is True and s["target"] == "1"
    assert s["answer"] == "full answer 1"
    assert s["perf"]["latency"] == 1.0 and s["perf"]["output_tokens"] == 300
    assert s["prompt"] == [{"role": "user", "content": "q1"}]


def test_sample_unknown_index_is_none(tmp_path):
    rd = _run(tmp_path)
    eval_reports._compact_rows.cache_clear()
    assert eval_reports.sample(rd, "gsm8k", 999) is None

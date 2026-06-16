"""Accuracy/quality eval routes: catalog, validation, admin gating."""
import pytest

pytestmark = pytest.mark.api


def test_list_eval_datasets_returns_tiered_catalog(client):
    body = client.get("/api/eval/datasets").json()
    keys = {d["key"] for d in body["datasets"]}
    assert {"gsm8k", "mmlu", "ceval", "ifeval"} <= keys
    for d in body["datasets"]:
        assert {"key", "label", "dataset_id", "tier"} <= d.keys()


def test_list_eval_runs_empty(client):
    body = client.get("/api/eval").json()
    assert body == {"busy": False, "running": 0, "queued": 0,
                    "budget": 32, "used_budget": 0, "runs": []}


def test_start_eval_unknown_dataset_is_400(client):
    resp = client.post("/api/eval", json={"model": "Qwen3-0.6B", "datasets": ["nope"]})
    assert resp.status_code == 400


def test_start_eval_unknown_model_is_400(client):
    resp = client.post("/api/eval", json={"model": "ghost", "datasets": ["gsm8k"]})
    assert resp.status_code == 400


def test_start_eval_requires_admin_when_enabled(auth_client):
    resp = auth_client.post("/api/eval", json={"model": "Qwen3-0.6B", "datasets": ["gsm8k"]})
    assert resp.status_code == 401


def test_start_eval_empty_datasets_is_422(client):
    # min_length=1 on datasets -> pydantic validation error
    resp = client.post("/api/eval", json={"model": "Qwen3-0.6B", "datasets": []})
    assert resp.status_code == 422


def test_start_eval_queues_behind_running_load_test(client, app):
    app.state.perf_manager._current = 99  # pretend a load test is in flight
    resp = client.post("/api/eval", json={"model": "Qwen3-0.6B", "datasets": ["gsm8k"]})
    # No longer a conflict — the eval is accepted and waits in the queue.
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"


def test_judge_dataset_without_judge_is_400(client):
    resp = client.post("/api/eval", json={"model": "Qwen3-0.6B", "datasets": ["simple_qa"]})
    assert resp.status_code == 400
    assert "judge" in resp.json()["detail"].lower()


def test_judge_enabled_without_model_is_400(client):
    resp = client.post("/api/eval", json={
        "model": "Qwen3-0.6B", "datasets": ["gsm8k"], "judge_enabled": True,
    })
    assert resp.status_code == 400


def test_external_judge_without_url_is_400(client):
    resp = client.post("/api/eval", json={
        "model": "Qwen3-0.6B", "datasets": ["gsm8k"], "judge_enabled": True,
        "judge_target": "external", "judge_model": "gpt-4o-mini",
    })
    assert resp.status_code == 400


def test_get_unknown_eval_run_is_404(client):
    assert client.get("/api/eval/999").status_code == 404


def test_delete_unknown_eval_run_is_404(client):
    assert client.delete("/api/eval/999").status_code == 404


async def test_eval_log_streams_while_running(client, app, tmp_path):
    # While running, output_dir is unset — the log must still stream from the
    # manager-derived run dir (regression: endpoint used to 404 until finish).
    app.state.eval_manager.eval_root = str(tmp_path)
    rid = await app.state.store.create_eval_run(
        model="Qwen3-0.6B", target_url="u", datasets="[]", params="{}", status="running",
    )
    run_dir = tmp_path / str(rid)
    run_dir.mkdir()
    (run_dir / "run.log").write_text("[runner] running… 5s elapsed\n", encoding="utf-8")
    resp = client.get(f"/api/eval/{rid}/log")
    assert resp.status_code == 200
    assert "5s elapsed" in resp.json()["content"]


async def _seed_run_with_outputs(app, tmp_path):
    """A completed run whose output dir has a minimal report + sample files."""
    import json
    app.state.eval_manager.eval_root = str(tmp_path)
    rid = await app.state.store.create_eval_run(
        model="Qwen3-0.6B", target_url="u", datasets='["gsm8k"]', params="{}", status="running",
    )
    rd = tmp_path / str(rid)
    (rd / "reports" / "m").mkdir(parents=True)
    (rd / "reports" / "m" / "gsm8k.json").write_text(json.dumps({
        "dataset_name": "gsm8k", "dataset_pretty_name": "GSM8K", "score": 0.5, "num": 2,
        "metrics": [{"name": "mean_acc", "score": 0.5, "num": 2, "categories": [
            {"subsets": [{"name": "main", "score": 0.5, "num": 2, "is_aggregate": False}]}]}],
        "perf_metrics": {"summary": {"n_samples": 2, "latency": {"mean": 5.0, "50%": 5.0, "99%": 5.0, "max": 5.0},
                                     "throughput": {"avg_output_tps": 40.0}, "usage": {}}},
    }), encoding="utf-8")
    (rd / "reviews" / "m").mkdir(parents=True)
    (rd / "reviews" / "m" / "gsm8k_main.jsonl").write_text(
        "\n".join(json.dumps({"index": i, "target": str(i), "messages": [],
            "sample_score": {"score": {"value": {"acc": float(i - 1)},
            "extracted_prediction": str(i), "prediction": f"a{i}"}}}) for i in (1, 2)),
        encoding="utf-8")
    return rid


async def test_eval_detail_returns_perf_and_subsets(client, app, tmp_path):
    from app.services import eval_reports
    eval_reports._compact_rows.cache_clear()
    rid = await _seed_run_with_outputs(app, tmp_path)
    body = client.get(f"/api/eval/{rid}/detail").json()
    [d] = body["datasets"]
    assert d["dataset"] == "gsm8k" and d["perf"]["output_tps"] == 40.0
    assert d["metrics"][0]["subsets"] == [{"name": "main", "score": 0.5, "num": 2}]


async def test_eval_samples_paginated_and_filtered(client, app, tmp_path):
    from app.services import eval_reports
    eval_reports._compact_rows.cache_clear()
    rid = await _seed_run_with_outputs(app, tmp_path)
    allp = client.get(f"/api/eval/{rid}/samples?dataset=gsm8k").json()
    assert allp["total"] == 2 and allp["total_correct"] == 1
    wrong = client.get(f"/api/eval/{rid}/samples?dataset=gsm8k&filter=wrong").json()
    assert wrong["total"] == 1 and wrong["samples"][0]["correct"] is False
    one = client.get(f"/api/eval/{rid}/samples/2?dataset=gsm8k").json()
    assert one["index"] == 2 and one["correct"] is True and one["target"] == "2"


def test_eval_samples_unknown_run_is_404(client):
    assert client.get("/api/eval/9999/samples?dataset=gsm8k").status_code == 404

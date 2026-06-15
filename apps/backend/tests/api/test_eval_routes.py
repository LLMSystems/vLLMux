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
    assert body == {"busy": False, "runs": []}


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


def test_start_eval_conflicts_with_running_load_test(client, app):
    app.state.perf_manager._current = 99  # pretend a load test is in flight
    resp = client.post("/api/eval", json={"model": "Qwen3-0.6B", "datasets": ["gsm8k"]})
    assert resp.status_code == 409


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

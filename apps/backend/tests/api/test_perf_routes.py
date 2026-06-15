"""Perf (load-test) routes: validation + admin gating (no subprocess spawned)."""
import pytest

pytestmark = pytest.mark.api


def test_list_runs_reports_busy_and_runs(client):
    body = client.get("/api/perf").json()
    assert body["busy"] is False
    assert isinstance(body["runs"], list)


def test_start_rejects_mismatched_lengths(client):
    resp = client.post("/api/perf", json={
        "model": "Qwen3-0.6B", "parallel": [1, 4], "number": [10],
    })
    assert resp.status_code == 400


def test_start_sla_requires_params(client):
    resp = client.post("/api/perf", json={"model": "Qwen3-0.6B", "mode": "sla"})
    assert resp.status_code == 400


def test_start_unknown_model_is_400(client):
    resp = client.post("/api/perf", json={
        "model": "nope", "parallel": [1], "number": [10],
    })
    assert resp.status_code == 400  # PerfError -> bad request


def test_start_requires_admin_when_enabled(auth_client):
    resp = auth_client.post("/api/perf", json={
        "model": "Qwen3-0.6B", "parallel": [1], "number": [10],
    })
    assert resp.status_code == 401


def test_get_unknown_run_is_404(client):
    assert client.get("/api/perf/999").status_code == 404

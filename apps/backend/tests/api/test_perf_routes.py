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


def test_start_openloop_requires_rate(client):
    resp = client.post("/api/perf", json={"model": "Qwen3-0.6B", "mode": "openloop", "number": [20]})
    assert resp.status_code == 400


def test_start_multiturn_custom_requires_path(client):
    resp = client.post("/api/perf", json={
        "model": "Qwen3-0.6B", "mode": "multiturn", "mt_dataset": "custom_multi_turn",
        "parallel": [4], "number": [20],
    })
    assert resp.status_code == 400


def test_start_embedding_requires_parallel(client):
    resp = client.post("/api/perf", json={"model": "m3e-base", "mode": "embedding"})
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


def test_start_blocked_while_eval_busy(client, app):
    # An eval running or queued holds the GPU — a load test must wait.
    app.state.eval_manager._queued.append(99)
    resp = client.post("/api/perf", json={
        "model": "Qwen3-0.6B", "parallel": [1], "number": [10],
    })
    assert resp.status_code == 409


def test_get_unknown_run_is_404(client):
    assert client.get("/api/perf/999").status_code == 404


async def test_perf_log_streams_while_running(client, app, tmp_path):
    app.state.perf_manager.perf_root = str(tmp_path)
    rid = await app.state.store.create_perf_run(
        model="Qwen3-0.6B", target_url="u", params="{}",
    )
    run_dir = tmp_path / str(rid)
    run_dir.mkdir()
    (run_dir / "run.log").write_text("[runner] running… 5s elapsed\n", encoding="utf-8")
    resp = client.get(f"/api/perf/{rid}/log")
    assert resp.status_code == 200
    assert "5s elapsed" in resp.json()["content"]

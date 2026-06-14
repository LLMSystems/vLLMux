import pytest

from app.llmops.state import ModelState

pytestmark = pytest.mark.api

KEY = "Qwen3-0.6B::qwen3"


def test_list_models_returns_all_configured_instances(client):
    resp = client.get("/api/models")
    assert resp.status_code == 200
    keys = {m["key"] for m in resp.json()}
    assert {"Qwen3-0.6B::qwen3", "Qwen3-0.6B::qwen3-2", "embedding::default"} <= keys
    # All start STOPPED until something is launched.
    assert all(m["state"] == ModelState.STOPPED.value for m in resp.json())


def test_get_unknown_model_is_404(client):
    assert client.get("/api/models/nope::nope").status_code == 404


def test_start_transitions_to_starting_and_returns_202(client):
    resp = client.post(f"/api/models/{KEY}/start")
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == ModelState.STARTING.value
    assert body["desired"] == "running"
    assert body["managed"] is True
    assert body["pid"] == 12345  # from FakeProc


def test_double_start_is_409(client):
    client.post(f"/api/models/{KEY}/start")
    resp = client.post(f"/api/models/{KEY}/start")
    assert resp.status_code == 409


def test_start_unknown_is_404(client):
    assert client.post("/api/models/nope::nope/start").status_code == 404


def test_start_pinned_to_missing_gpu_is_409(client, monkeypatch):
    # Only GPU 0 exists; embedding_server is pinned to cuda_device 1 in the
    # fake config, so its start must be blocked before spawning (no crash loop).
    from app.services import gpu_service

    monkeypatch.setattr(
        gpu_service, "get_gpu_info",
        lambda: [{"index": 0, "memory_total": 100_000, "memory_used": 0}],
    )
    resp = client.post("/api/models/embedding::default/start")
    assert resp.status_code == 409
    assert "GPU 1 not found" in resp.json()["detail"]
    # A model pinned to an existing GPU still starts fine.
    assert client.post(f"/api/models/{KEY}/start").status_code == 202


def test_stop_after_start_returns_stopped(client):
    client.post(f"/api/models/{KEY}/start")
    resp = client.post(f"/api/models/{KEY}/stop")
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == ModelState.STOPPED.value
    assert body["desired"] == "stopped"
    assert body["pid"] is None


def test_stop_unknown_is_404(client):
    assert client.post("/api/models/nope::nope/stop").status_code == 404


def test_update_embedding_unknown_model_is_404(client):
    resp = client.put(
        "/api/embedding/models",
        json={"model_type": "embedding", "name": "nope", "settings": {"max_length": 128}},
    )
    assert resp.status_code == 404


def test_update_embedding_requires_admin(auth_client):
    resp = auth_client.put(
        "/api/embedding/models",
        json={"model_type": "embedding", "name": "m3e-base", "settings": {"max_length": 128}},
    )
    assert resp.status_code == 401

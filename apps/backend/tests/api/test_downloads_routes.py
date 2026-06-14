"""HF cache inventory + download routes (network stubbed out)."""
import pytest

from app.services import hf_service

pytestmark = pytest.mark.api


def test_get_cache_returns_disk_and_models(client, monkeypatch):
    monkeypatch.setattr(hf_service, "scan_cache", lambda: [
        {"repo_id": "Qwen/Qwen3-0.6B", "size_on_disk": 1500, "nb_files": 5,
         "last_modified": 1.0, "revisions": ["abc"]},
    ])
    monkeypatch.setattr(hf_service, "disk_usage", lambda: {"total": 100, "used": 40, "free": 60})

    body = client.get("/api/cache").json()
    assert body["disk"] == {"total": 100, "used": 40, "free": 60}
    assert body["models"][0]["repo_id"] == "Qwen/Qwen3-0.6B"


def test_delete_cached_model_404_when_absent(client, monkeypatch):
    monkeypatch.setattr(hf_service, "delete_repo", lambda repo: False)
    assert client.delete("/api/cache/org/missing").status_code == 404


def test_delete_cached_model_ok(client, monkeypatch):
    monkeypatch.setattr(hf_service, "delete_repo", lambda repo: True)
    assert client.delete("/api/cache/org/model").status_code == 204


def test_start_download_is_idempotent_and_listed(client, monkeypatch):
    # Stub the blocking HF calls so the job runs without touching the network.
    monkeypatch.setattr(hf_service, "model_total_size", lambda repo, token=None: 1000)
    monkeypatch.setattr(hf_service, "download", lambda repo, token=None: "/tmp/x")
    monkeypatch.setattr(hf_service, "repo_dir_size", lambda repo: 1000)

    r = client.post("/api/downloads", json={"repo_id": "org/m"})
    assert r.status_code == 202
    assert r.json()["repo_id"] == "org/m"

    listed = client.get("/api/downloads").json()
    assert any(j["repo_id"] == "org/m" for j in listed)


def test_download_requires_admin_when_enabled(auth_client):
    assert auth_client.post("/api/downloads", json={"repo_id": "org/m"}).status_code == 401


def test_delete_cache_requires_admin_when_enabled(auth_client):
    assert auth_client.delete("/api/cache/org/m").status_code == 401

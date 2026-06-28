"""Config export / import / version-history / rollback routes + auto-snapshot.

Rebuilds the manager against a temp config.yaml + overlay so build_merged_config
and the registry agree on disk; the version-snapshot middleware (installed on the
real app) then records a snapshot for every mutation that changes the overlay.
"""
import pytest

from app.core.settings import BackendSettings
from app.llmops.launchers import EmbeddingLauncher, VllmLauncher
from app.llmops.manager import ModelManager, build_registry
from app.llmops.state import ModelState
from schema import load_config

pytestmark = pytest.mark.api

ADMIN = {"Authorization": "Bearer secret-admin"}

CONFIG_YAML = """
server:
  port: 8887
LLM_engines:
  Qwen3-0.6B:
    instances:
      - id: a
        host: localhost
        port: 8002
        cuda_device: 0
    model_config:
      model_tag: Qwen/Qwen3-0.6B
      max_model_len: 500
      gpu_memory_utilization: 0.35
"""

EXTRA_OVERLAY = {"LLM_engines": {"Extra": {
    "instances": [{"id": "x", "host": "localhost", "port": 9300, "cuda_device": 0}],
    "model_config": {"model_tag": "some/Model", "max_model_len": 256,
                     "gpu_memory_utilization": 0.4},
}}}


def _setup(auth_client, tmp_path, monkeypatch):
    """Point the live app's manager + the snapshot middleware at temp files."""
    app = auth_client.app
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")
    overlay = tmp_path / "overlay.json"
    monkeypatch.setenv("LLMOPS_OVERLAY_PATH", str(overlay))  # middleware reads this
    config = load_config(str(cfg))
    launchers = [VllmLauncher(), EmbeddingLauncher()]
    registry = build_registry(config, str(cfg), launchers)
    app.state.config = config
    app.state.config_path = str(cfg)
    app.state.registry = registry
    app.state.manager = ModelManager(
        registry, launchers, app.state.http_client, config, str(cfg),
        app.state.settings, store=app.state.store, overlay_path=str(overlay),
        router_url="http://router",
    )
    return app


def _mint(auth_client, label, role):
    r = auth_client.post("/api/operators", json={"label": label, "role": role}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["token"]


# -- export / import ---------------------------------------------------------

def test_export_then_import_round_trips(auth_client, tmp_path, monkeypatch):
    _setup(auth_client, tmp_path, monkeypatch)
    exp = auth_client.get("/api/config/export", headers=ADMIN)
    assert exp.status_code == 200
    body = exp.json()
    assert body["version"] == 1 and "overlay" in body

    imp = auth_client.post("/api/config/import", json={"overlay": EXTRA_OVERLAY}, headers=ADMIN)
    assert imp.status_code == 200, imp.text
    assert imp.json()["added"] == ["Extra::x"]
    # Now exporting reflects the imported group.
    again = auth_client.get("/api/config/export", headers=ADMIN).json()
    assert "Extra" in again["overlay"]["LLM_engines"]


def test_import_accepts_bare_overlay(auth_client, tmp_path, monkeypatch):
    _setup(auth_client, tmp_path, monkeypatch)
    imp = auth_client.post("/api/config/import", json=EXTRA_OVERLAY, headers=ADMIN)
    assert imp.status_code == 200 and imp.json()["added"] == ["Extra::x"]


def test_import_invalid_is_400(auth_client, tmp_path, monkeypatch):
    _setup(auth_client, tmp_path, monkeypatch)
    bad = {"overlay": {"LLM_engines": {"Bad": {"instances": [{"id": "z"}]}}}}
    assert auth_client.post("/api/config/import", json=bad, headers=ADMIN).status_code == 400


def test_import_running_conflict_is_409(auth_client, tmp_path, monkeypatch):
    app = _setup(auth_client, tmp_path, monkeypatch)
    app.state.registry.get("Qwen3-0.6B::a").state = ModelState.READY
    body = {"overlay": {"LLM_engines": {"Qwen3-0.6B": {
        "instances": [{"id": "a", "host": "localhost", "port": 9999, "cuda_device": 0}]}}}}
    assert auth_client.post("/api/config/import", json=body, headers=ADMIN).status_code == 409
    # force overrides it.
    assert auth_client.post(
        "/api/config/import?force=true", json=body, headers=ADMIN).status_code == 200


# -- RBAC --------------------------------------------------------------------

def test_export_is_operator_import_is_admin(auth_client, tmp_path, monkeypatch):
    _setup(auth_client, tmp_path, monkeypatch)
    op = _mint(auth_client, "op", "operator")
    viewer = _mint(auth_client, "v", "viewer")
    h_op = {"Authorization": f"Bearer {op}"}
    # operator can export, viewer cannot.
    assert auth_client.get("/api/config/export", headers=h_op).status_code == 200
    assert auth_client.get(
        "/api/config/export", headers={"Authorization": f"Bearer {viewer}"}).status_code == 403
    # operator cannot import (admin surface).
    assert auth_client.post(
        "/api/config/import", json=EXTRA_OVERLAY, headers=h_op).status_code == 403


# -- version history + rollback ---------------------------------------------

def test_mutation_snapshots_and_rollback(auth_client, tmp_path, monkeypatch):
    _setup(auth_client, tmp_path, monkeypatch)
    # A mutation through the import route is auto-snapshotted by the middleware.
    auth_client.post("/api/config/import", json=EXTRA_OVERLAY, headers=ADMIN)

    versions = auth_client.get("/api/config/versions", headers=ADMIN)
    assert versions.status_code == 200
    rows = versions.json()["versions"]
    assert rows and rows[0]["summary"].startswith("POST /api/config/import")
    assert rows[0]["is_current"] is True
    vid = rows[0]["id"]

    # Fetch the full snapshot.
    one = auth_client.get(f"/api/config/versions/{vid}", headers=ADMIN).json()
    assert "Extra" in one["overlay"]["LLM_engines"]

    # diff against the live overlay (identical -> equal text).
    diff = auth_client.get(f"/api/config/versions/{vid}/diff", headers=ADMIN).json()
    assert diff["to"]["label"] == "current"

    # Wipe the overlay, then roll back to the snapshot that had Extra.
    auth_client.post("/api/config/import", json={"overlay": {"LLM_engines": {}}}, headers=ADMIN)
    assert auth_client.app.state.registry.get("Extra::x") is None
    rb = auth_client.post(f"/api/config/versions/{vid}/rollback", headers=ADMIN)
    assert rb.status_code == 200 and rb.json()["rolled_back_to"] == vid
    assert auth_client.app.state.registry.get("Extra::x") is not None


def test_versions_list_is_operator_rollback_is_admin(auth_client, tmp_path, monkeypatch):
    _setup(auth_client, tmp_path, monkeypatch)
    auth_client.post("/api/config/import", json=EXTRA_OVERLAY, headers=ADMIN)
    op = _mint(auth_client, "op2", "operator")
    h_op = {"Authorization": f"Bearer {op}"}
    vid = auth_client.get("/api/config/versions", headers=h_op).json()["versions"][0]["id"]
    # operator reads history but cannot roll back.
    assert auth_client.post(
        f"/api/config/versions/{vid}/rollback", headers=h_op).status_code == 403

"""Liveness (/health) and readiness (/ready) probes for k8s / load balancers."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.llm_router.router import router as llm_router

pytestmark = pytest.mark.unit

CONFIG = {"LLM_engines": {"Qwen3-0.6B": {"instances": [{"id": "a", "host": "h", "port": 8002}]}}}


def _client(*, config=CONFIG, started=True, live_addrs=None):
    app = FastAPI()
    app.include_router(llm_router)
    app.state.config = config
    app.state.live_addrs = live_addrs or {}
    if started:
        app.state.http_client = object()  # lifespan-completed marker
    return TestClient(app)


def test_health_is_always_ok():
    r = _client().get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_health_needs_no_state():
    # Liveness must not depend on config/startup — a bare app still answers 200.
    app = FastAPI()
    app.include_router(llm_router)
    assert TestClient(app).get("/health").status_code == 200


def test_ready_when_config_loaded_and_started():
    r = _client().get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "groups": 1, "routable_instances": 0}


def test_ready_reports_routable_instance_count():
    # HA Phase 3e: a stateless replica surfaces how many instances it can route to
    # (live addresses from the shared store), without gating readiness on it.
    r = _client(live_addrs={("Qwen3-0.6B", "a"): ("h", 8002)}).get("/ready")
    assert r.status_code == 200
    assert r.json()["routable_instances"] == 1


def test_ready_503_before_startup_finishes():
    r = _client(started=False).get("/ready")
    assert r.status_code == 503 and r.json()["reason"] == "starting"


def test_ready_503_without_config():
    r = _client(config=None).get("/ready")
    assert r.status_code == 503 and r.json()["reason"] == "config not loaded"

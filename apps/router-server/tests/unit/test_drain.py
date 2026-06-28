"""Graceful drain on the router: drained instances take no new requests (while a
non-draining alternative exists), and the /drain & /undrain control endpoints."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.llm_router.backend_runtime_state import (clear_draining, is_draining,
                                                  mark_draining)
from src.llm_router.router import router as llm_router
from src.llm_router.routing_strategies import select_instance

pytestmark = pytest.mark.unit

CFG = {
    "instances": [
        {"id": "a", "host": "h", "port": 8002},
        {"id": "b", "host": "h", "port": 8003},
    ]
}


def _app(**state):
    ns = type("S", (), {})()
    ns.metrics_cache = {}
    ns.backend_inflight = {}
    ns.backend_health = {}
    ns.rr_counters = {}
    ns.draining = {}
    ns.routing_strategy = "least_load"
    for k, v in state.items():
        setattr(ns, k, v)
    return type("App", (), {"state": ns})()


async def test_selector_skips_drained_instance():
    app = _app()
    mark_draining(app, "M", "a")
    # 'a' is draining and 'b' is healthy -> always pick 'b'.
    for _ in range(5):
        chosen = await select_instance(app, "M", CFG)
        assert chosen["id"] == "b"


async def test_selector_keeps_draining_when_it_is_the_only_option():
    app = _app()
    mark_draining(app, "M", "a")
    mark_draining(app, "M", "b")
    # All draining -> best-effort: still serve rather than 503.
    chosen = await select_instance(app, "M", CFG)
    assert chosen["id"] in {"a", "b"}


def test_is_draining_expires():
    app = _app()
    app.state.draining["M::a"] = time.time() - 1  # already expired
    assert is_draining(app, "M", "a") is False
    assert "M::a" not in app.state.draining  # self-healed
    clear_draining(app, "M", "a")  # idempotent


def _client():
    app = FastAPI()
    app.include_router(llm_router)
    app.state.draining = {}
    app.state.backend_inflight = {"M::a": 2}
    return app, TestClient(app)


def test_drain_endpoint_marks_and_reports_inflight():
    app, client = _client()
    r = client.post("/drain", json={"model_key": "M", "instance_id": "a"})
    assert r.status_code == 200
    assert r.json() == {"draining": True, "inflight": 2}
    assert "M::a" in app.state.draining

    assert client.post("/undrain", json={"model_key": "M", "instance_id": "a"}).json() == {
        "draining": False
    }
    assert "M::a" not in app.state.draining


def test_drain_requires_fields():
    _, client = _client()
    assert client.post("/drain", json={"model_key": "M"}).status_code == 400

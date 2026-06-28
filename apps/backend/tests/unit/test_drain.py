"""Graceful drain on stop: the backend asks the router to quiesce an instance and
waits for in-flight to clear before killing the process (HA Phase 1)."""
import pytest

from app.core.settings import BackendSettings
from app.llmops.state import ModelState
from tests.conftest import FakeProc

pytestmark = pytest.mark.unit


def _running(app, key="Qwen3-0.6B::qwen3"):
    inst = app.state.manager.registry.get(key)
    inst.proc = FakeProc()
    inst.managed = True
    inst.set_state(ModelState.READY)
    return inst


async def test_stop_drains_before_kill(app):
    mgr = app.state.manager
    mgr.router_url = "http://router"
    http = app.state.http_client
    http.drain_inflight = 0  # nothing in flight -> drain returns immediately
    inst = _running(app)

    await mgr.stop("Qwen3-0.6B::qwen3")

    drains = [j for (u, j) in http.posts if u.endswith("/drain")]
    assert drains and drains[0] == {"model_key": "Qwen3-0.6B", "instance_id": "qwen3",
                                    "ttl": mgr.settings.drain_timeout + 10}
    assert inst.state == ModelState.STOPPED


async def test_stop_honors_drain_timeout_when_inflight_never_clears(app):
    mgr = app.state.manager
    mgr.router_url = "http://router"
    mgr.settings = BackendSettings(drain_timeout=0.05, drain_poll_interval=0.01)
    http = app.state.http_client
    http.drain_inflight = 3  # stays busy -> drain must time out, not hang
    _running(app)

    await mgr.stop("Qwen3-0.6B::qwen3")  # returns despite in-flight

    assert mgr.registry.get("Qwen3-0.6B::qwen3").state == ModelState.STOPPED
    assert any(u.endswith("/drain") for u, _ in http.posts)


async def test_stop_skips_drain_without_router(app):
    mgr = app.state.manager
    mgr.router_url = None  # no router configured -> no drain attempt
    http = app.state.http_client
    _running(app)

    await mgr.stop("Qwen3-0.6B::qwen3")

    assert not any(u.endswith("/drain") for u, _ in http.posts)
    assert mgr.registry.get("Qwen3-0.6B::qwen3").state == ModelState.STOPPED


async def test_start_undrains(app, monkeypatch):
    from app.llmops import manager as manager_mod

    monkeypatch.setattr(manager_mod, "spawn_process", lambda spec: FakeProc())
    mgr = app.state.manager
    mgr.router_url = "http://router"
    http = app.state.http_client
    inst = mgr.registry.get("Qwen3-0.6B::qwen3")
    inst.set_state(ModelState.STOPPED)

    await mgr.start("Qwen3-0.6B::qwen3")

    assert any(u.endswith("/undrain") for u, _ in http.posts)

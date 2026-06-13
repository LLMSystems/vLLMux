import pytest

from app.api import observability
from app.llmops.state import ModelState

pytestmark = pytest.mark.api

KEY = "Qwen3-0.6B::qwen3"


def test_start_records_state_event(client):
    client.post(f"/api/models/{KEY}/start")
    events = client.app.state.store.events
    assert (KEY, "llm", "stopped", "starting", None) in events


def test_stop_records_event(client):
    client.post(f"/api/models/{KEY}/start")
    client.post(f"/api/models/{KEY}/stop")
    transitions = [(e[2], e[3]) for e in client.app.state.store.events if e[0] == KEY]
    assert ("starting", "stopping") in transitions
    assert ("stopping", "stopped") in transitions


def test_get_events_endpoint(client):
    client.post(f"/api/models/{KEY}/start")
    resp = client.get("/api/events")
    assert resp.status_code == 200
    assert any(e["key"] == KEY and e["to_state"] == "starting" for e in resp.json())


def test_model_events_404_for_unknown(client):
    assert client.get("/api/models/nope::nope/events").status_code == 404


def test_usage_endpoint(client):
    # Seed a request log directly on the fake store.
    client.app.state.store.reqs.append({"model_key": "Qwen3-0.6B", "status_code": 200})
    resp = client.get("/api/usage")
    assert resp.status_code == 200
    summary = {r["model_key"]: r for r in resp.json()}
    assert summary["Qwen3-0.6B"]["count"] == 1


def test_requests_endpoint(client):
    client.app.state.store.reqs.append({"model_key": "Qwen3-0.6B", "path": "/v1/chat/completions"})
    resp = client.get("/api/requests?model_key=Qwen3-0.6B")
    assert resp.status_code == 200
    assert resp.json()[0]["path"] == "/v1/chat/completions"


def test_model_logs_endpoint(client, tmp_path):
    log = tmp_path / "qwen.log"
    log.write_text("line1\nline2\nline3\n", encoding="utf-8")
    client.app.state.registry.get(KEY).log_path = str(log)

    resp = client.get(f"/api/models/{KEY}/logs?tail=2")
    assert resp.status_code == 200
    assert resp.json()["content"] == "line2\nline3\n"


def test_model_logs_404_when_no_file(client):
    client.app.state.registry.get(KEY).log_path = "/nonexistent/path.log"
    assert client.get(f"/api/models/{KEY}/logs").status_code == 404


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["models"].get("stopped", 0) >= 1


async def test_sse_generator_emits_snapshot_on_change():
    """Test the SSE generator directly (avoids streaming a forever-loop endpoint)."""
    from tests.conftest import FAKE_CONFIG
    from app.llmops.launchers import VllmLauncher
    from app.llmops.manager import build_registry

    registry = build_registry(FAKE_CONFIG, "config.yaml", [VllmLauncher()])
    gen = observability.model_snapshot_stream(registry, interval=0.01)

    first = await gen.__anext__()  # initial snapshot always emitted
    assert first.startswith("data: ")
    assert "Qwen3-0.6B::qwen3" in first

    # Mutate state -> next emit reflects it.
    registry.get("Qwen3-0.6B::qwen3").set_state(ModelState.READY)
    nxt = await gen.__anext__()
    assert '"state": "ready"' in nxt
    await gen.aclose()

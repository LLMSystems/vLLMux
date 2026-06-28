"""Unit tests for the shared chat/completions proxy in router.py.

These exercise the dedup + the /v1/completions model_tag bug fix without a live
vLLM: the upstream HTTP client is faked, and a single-instance config makes the
selector deterministic (it returns the only instance without needing metrics).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.llm_router.router import router as llm_router

pytestmark = pytest.mark.unit

CONFIG = {
    "LLM_engines": {
        "Qwen3-0.6B": {
            "instances": [{"id": "qwen3", "host": "localhost", "port": 8002}],
            "model_config": {"model_tag": "Qwen/Qwen3-0.6B"},
        }
    }
}


class FakeResponse:
    def __init__(self, status_code=200, content=b'{"ok": true}', content_type="application/json"):
        self.status_code = status_code
        self._content = content
        self.headers = {"content-type": content_type}

    async def aread(self):
        return self._content

    async def aiter_raw(self):
        yield self._content


class FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class FakeHTTPClient:
    """Records the upstream call and returns a canned response."""

    def __init__(self, response=None):
        self._response = response or FakeResponse()
        self.calls = []

    def stream(self, method, url, json=None):
        self.calls.append({"method": method, "url": url, "json": json})
        return FakeStreamCtx(self._response)


class SSEResponse:
    """A streaming (text/event-stream) response that yields raw bytes in pieces,
    so the proxy's usage-sniffing has to reassemble events across chunks."""

    def __init__(self, chunks, status_code=200):
        self.status_code = status_code
        self._chunks = chunks
        self.headers = {"content-type": "text/event-stream"}

    async def aiter_raw(self):
        for c in self._chunks:
            yield c


class RaisingStreamCtx:
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc):
        return False


class SequencedHTTPClient:
    """Returns queued outcomes (a response to serve, or an exception to raise on
    connect) one per stream() call, to drive failover paths."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def stream(self, method, url, json=None):
        self.calls.append({"method": method, "url": url, "json": json})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            return RaisingStreamCtx(outcome)
        return FakeStreamCtx(outcome)


class FakeStore:
    def __init__(self):
        self.reqs = []

    async def record_request(self, **kwargs):
        self.reqs.append(kwargs)


CONFIG_2 = {
    "LLM_engines": {
        "Qwen3-0.6B": {
            "instances": [
                {"id": "a", "host": "localhost", "port": 8002},
                {"id": "b", "host": "localhost", "port": 8004},
            ],
            "model_config": {"model_tag": "Qwen/Qwen3-0.6B"},
        }
    }
}


CONFIG_FB = {
    "LLM_engines": {
        "A": {
            "instances": [{"id": "a1", "host": "localhost", "port": 9001}],
            "model_config": {"model_tag": "org/a"},
            "fallback": ["B"],
        },
        "B": {
            "instances": [{"id": "b1", "host": "localhost", "port": 9002}],
            "model_config": {"model_tag": "org/b"},
        },
    }
}


def build_client(config, http_client):
    app = FastAPI()
    app.include_router(llm_router)
    app.state.config = config
    app.state.http_client = http_client
    app.state.store = FakeStore()
    app.state.metrics_cache = {}
    app.state.backend_inflight = {}
    app.state.backend_health = {}
    return TestClient(app)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(llm_router)
    app.state.config = CONFIG
    app.state.http_client = FakeHTTPClient()
    app.state.store = FakeStore()
    app.state.metrics_cache = {}
    app.state.backend_inflight = {}
    app.state.backend_health = {}
    return TestClient(app)


@pytest.mark.parametrize(
    "path,upstream",
    [
        ("/v1/chat/completions", "/v1/chat/completions"),
        ("/v1/completions", "/v1/completions"),  # was broken before the fix
    ],
)
def test_proxy_forwards_and_rewrites_model_tag(client, path, upstream):
    resp = client.post(path, json={"model": "Qwen3-0.6B", "prompt": "hi"})
    assert resp.status_code == 200

    call = client.app.state.http_client.calls[-1]
    assert call["url"] == f"http://localhost:8002{upstream}"
    # Group key rewritten to the served model_tag before forwarding.
    assert call["json"]["model"] == "Qwen/Qwen3-0.6B"
    # Inflight accounting balanced after a buffered response.
    assert client.app.state.backend_inflight == {}
    # The request was logged to the shared store with the right shape.
    reqs = client.app.state.store.reqs
    assert len(reqs) == 1
    assert reqs[0]["model_key"] == "Qwen3-0.6B"
    assert reqs[0]["path"] == upstream
    assert reqs[0]["status_code"] == 200
    assert reqs[0]["instance_id"] == "qwen3"


def test_missing_model_is_400(client):
    assert client.post("/v1/completions", json={"prompt": "hi"}).status_code == 400


def test_unknown_model_is_404(client):
    resp = client.post("/v1/chat/completions", json={"model": "nope", "prompt": "hi"})
    assert resp.status_code == 404


def test_list_models_returns_configured_groups(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert ids == ["Qwen3-0.6B"]


def test_failover_on_5xx_picks_another_instance():
    # First-tried instance returns 503, the proxy should fail over to the second.
    http = SequencedHTTPClient([FakeResponse(status_code=503), FakeResponse(status_code=200)])
    client = build_client(CONFIG_2, http)
    resp = client.post("/v1/chat/completions", json={"model": "Qwen3-0.6B", "prompt": "hi"})
    assert resp.status_code == 200
    # Both backends were tried, on different ports.
    ports = [c["url"] for c in http.calls]
    assert len(ports) == 2 and ports[0] != ports[1]
    assert client.app.state.backend_inflight == {}  # balanced after failover


def test_failover_on_transport_error():
    http = SequencedHTTPClient([ConnectionError("refused"), FakeResponse(status_code=200)])
    client = build_client(CONFIG_2, http)
    resp = client.post("/v1/chat/completions", json={"model": "Qwen3-0.6B", "prompt": "hi"})
    assert resp.status_code == 200
    assert len(http.calls) == 2
    assert client.app.state.backend_inflight == {}


def test_cross_group_fallback_serves_from_next_group():
    # Group A's only instance is unreachable -> fall back to group B, which serves.
    http = SequencedHTTPClient([ConnectionError("A down"), FakeResponse(status_code=200)])
    client = build_client(CONFIG_FB, http)
    resp = client.post("/v1/chat/completions", json={"model": "A", "prompt": "hi"})
    assert resp.status_code == 200
    assert len(http.calls) == 2
    # Second hop went to B's port, forwarding B's own model_tag.
    assert ":9002" in http.calls[1]["url"]
    assert http.calls[1]["json"]["model"] == "org/b"
    # Logged under the group that actually served (B); accounting balanced.
    assert client.app.state.store.reqs[-1]["model_key"] == "B"
    assert client.app.state.backend_inflight == {}


def test_no_fallback_when_primary_serves():
    http = SequencedHTTPClient([FakeResponse(status_code=200)])
    client = build_client(CONFIG_FB, http)
    resp = client.post("/v1/chat/completions", json={"model": "A", "prompt": "hi"})
    assert resp.status_code == 200
    assert len(http.calls) == 1 and ":9001" in http.calls[0]["url"]  # only A touched
    assert client.app.state.store.reqs[-1]["model_key"] == "A"


def test_fallback_chain_all_down_returns_503():
    http = SequencedHTTPClient([ConnectionError("A"), ConnectionError("B")])
    client = build_client(CONFIG_FB, http)
    resp = client.post("/v1/chat/completions", json={"model": "A", "prompt": "hi"})
    assert resp.status_code == 503
    assert len(http.calls) == 2  # tried A then B
    assert client.app.state.backend_inflight == {}


def test_all_backends_down_returns_503():
    http = SequencedHTTPClient([ConnectionError("x"), ConnectionError("y")])
    client = build_client(CONFIG_2, http)
    resp = client.post("/v1/chat/completions", json={"model": "Qwen3-0.6B", "prompt": "hi"})
    assert resp.status_code == 503
    assert client.app.state.backend_inflight == {}


def test_final_attempt_5xx_is_surfaced():
    # Both instances 5xx -> client gets the 5xx (no infinite retry), balanced.
    http = SequencedHTTPClient([FakeResponse(status_code=502), FakeResponse(status_code=502)])
    client = build_client(CONFIG_2, http)
    resp = client.post("/v1/chat/completions", json={"model": "Qwen3-0.6B", "prompt": "hi"})
    assert resp.status_code == 502
    assert client.app.state.backend_inflight == {}


def test_routes_to_live_address_when_published():
    # HA Phase 3a: when a live address is published for the instance, the proxy
    # routes there instead of the config (localhost) address — the basis for
    # cross-host routing. Config still decides *which* instance exists.
    http = FakeHTTPClient()
    client = build_client(CONFIG, http)
    client.app.state.live_addrs = {("Qwen3-0.6B", "qwen3"): ("10.1.2.3", 9100)}
    resp = client.post("/v1/chat/completions", json={"model": "Qwen3-0.6B", "prompt": "hi"})
    assert resp.status_code == 200
    assert http.calls[-1]["url"] == "http://10.1.2.3:9100/v1/chat/completions"


def test_falls_back_to_config_address_without_live_entry():
    # No live entry for this instance -> config localhost:8002, exactly as before.
    http = FakeHTTPClient()
    client = build_client(CONFIG, http)
    client.app.state.live_addrs = {("OtherGroup", "x"): ("10.0.0.9", 9999)}
    resp = client.post("/v1/chat/completions", json={"model": "Qwen3-0.6B", "prompt": "hi"})
    assert resp.status_code == 200
    assert http.calls[-1]["url"] == "http://localhost:8002/v1/chat/completions"


def test_streaming_usage_is_logged():
    # Usage event is split across raw chunks to exercise SSE reassembly.
    chunks = [
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_to',
        b'kens":5,"completion_tokens":7,"total_tokens":12}}\n\n'
        b"data: [DONE]\n\n",
    ]
    http = FakeHTTPClient(SSEResponse(chunks))
    client = build_client(CONFIG, http)
    with client.stream("POST", "/v1/chat/completions", json={"model": "Qwen3-0.6B", "stream": True}) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes())
    assert b"[DONE]" in body
    # The backend was asked to include usage, and tokens were logged.
    assert http.calls[-1]["json"]["stream_options"] == {"include_usage": True}
    reqs = client.app.state.store.reqs
    assert len(reqs) == 1
    assert reqs[0]["prompt_tokens"] == 5
    assert reqs[0]["completion_tokens"] == 7
    assert reqs[0]["total_tokens"] == 12

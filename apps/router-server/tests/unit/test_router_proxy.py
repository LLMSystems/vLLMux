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


class FakeStore:
    def __init__(self):
        self.reqs = []

    async def record_request(self, **kwargs):
        self.reqs.append(kwargs)


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

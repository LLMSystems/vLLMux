"""Unit tests for pooling-endpoint dispatch (/v1/embeddings, /v1/rerank, /v1/score).

A pooling model declared in LLM_engines (model_config.kind == embed|rerank) is
routed through the full backend machinery (load-balancing/failover/metrics),
exactly like a chat group. Anything not in LLM_engines falls back to the bespoke
embedding server. Kind mismatches are rejected.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.llm_router.router import router as llm_router

pytestmark = pytest.mark.unit


CONFIG = {
    "LLM_engines": {
        "chatA": {
            "instances": [{"id": "a", "host": "localhost", "port": 8002}],
            "model_config": {"model_tag": "org/chatA"},
        },
        "bge-m3": {
            "instances": [{"id": "e", "host": "localhost", "port": 8020}],
            "model_config": {"model_tag": "BAAI/bge-m3", "kind": "embed"},
        },
        "ms-marco": {
            "instances": [{"id": "r", "host": "localhost", "port": 8021}],
            "model_config": {"model_tag": "cross/ms-marco", "kind": "rerank"},
        },
    },
    "embedding_server": {"host": "localhost", "port": 8005},
}


class FakeResponse:
    def __init__(self, status_code=200, content=b'{"ok": true}', content_type="application/json"):
        self.status_code = status_code
        self._content = content
        self.content = content
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
    """Supports both .stream (backend pooling path) and .post (bespoke fallback)."""

    def __init__(self):
        self.stream_calls = []
        self.post_calls = []

    def stream(self, method, url, json=None):
        self.stream_calls.append({"method": method, "url": url, "json": json})
        return FakeStreamCtx(FakeResponse())

    async def post(self, url, content=None, headers=None, timeout=None):
        self.post_calls.append({"url": url, "content": content})
        return FakeResponse()


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


def test_embed_model_routes_to_pooling_backend(client):
    resp = client.post("/v1/embeddings", json={"model": "bge-m3", "input": "hi"})
    assert resp.status_code == 200
    http = client.app.state.http_client
    # Went through the backend (stream) path to the pooling instance's port,
    # with the model rewritten to its served model_tag — not the bespoke server.
    assert http.post_calls == []
    call = http.stream_calls[-1]
    assert call["url"] == "http://localhost:8020/v1/embeddings"
    assert call["json"]["model"] == "BAAI/bge-m3"


def test_rerank_model_routes_to_pooling_backend(client):
    resp = client.post("/v1/rerank", json={"model": "ms-marco", "query": "q", "documents": ["a", "b"]})
    assert resp.status_code == 200
    http = client.app.state.http_client
    call = http.stream_calls[-1]
    assert call["url"] == "http://localhost:8021/v1/rerank"
    assert call["json"]["documents"] == ["a", "b"]  # body forwarded intact


def test_unknown_model_falls_back_to_bespoke_server(client):
    # Not an LLM_engines group -> the bespoke embedding server (port 8005).
    resp = client.post("/v1/embeddings", json={"model": "m3e-base", "input": "hi"})
    assert resp.status_code == 200
    http = client.app.state.http_client
    assert http.stream_calls == []
    assert http.post_calls[-1]["url"] == "http://localhost:8005/v1/embeddings"


def test_embed_model_on_rerank_is_404(client):
    resp = client.post("/v1/rerank", json={"model": "bge-m3", "query": "q", "documents": ["a"]})
    assert resp.status_code == 404


def test_rerank_model_on_embeddings_is_404(client):
    resp = client.post("/v1/embeddings", json={"model": "ms-marco", "input": "hi"})
    assert resp.status_code == 404


def test_pooling_model_on_chat_is_404(client):
    resp = client.post("/v1/chat/completions", json={"model": "bge-m3", "messages": []})
    assert resp.status_code == 404


def test_chat_model_on_embeddings_is_404(client):
    # A generate group has no embeddings endpoint.
    resp = client.post("/v1/embeddings", json={"model": "chatA", "input": "hi"})
    assert resp.status_code == 404


def test_list_models_reports_kind(client):
    data = client.get("/v1/models").json()["data"]
    kinds = {m["id"]: m["kind"] for m in data}
    assert kinds == {"chatA": "chat", "bge-m3": "embed", "ms-marco": "rerank"}

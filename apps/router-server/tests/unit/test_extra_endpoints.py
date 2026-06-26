"""Unit tests for the Anthropic Messages + tokenize/detokenize proxies.

These ride the same _proxy_to_backend machinery as chat/completions (model_tag
rewrite, instance selection, logging), so the tests focus on what's specific:
  - /v1/messages + /v1/messages/count_tokens are chat-only (404 a pooling group);
  - /v1/messages must NOT get the OpenAI-only stream_options.include_usage knob;
  - /tokenize + /detokenize are kind-agnostic (work on a pooling group too).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.llm_router.router import router as llm_router
from src.llm_router.router import _normalize_usage, _usage_from_body

pytestmark = pytest.mark.unit


CONFIG = {
    "LLM_engines": {
        "Qwen3-0.6B": {
            "instances": [{"id": "qwen3", "host": "localhost", "port": 8002}],
            "model_config": {"model_tag": "Qwen/Qwen3-0.6B"},
        },
        "bge-m3": {
            "instances": [{"id": "e", "host": "localhost", "port": 8020}],
            "model_config": {"model_tag": "BAAI/bge-m3", "kind": "embed"},
        },
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


class SSEResponse:
    def __init__(self, chunks, status_code=200):
        self.status_code = status_code
        self._chunks = chunks
        self.headers = {"content-type": "text/event-stream"}

    async def aiter_raw(self):
        for c in self._chunks:
            yield c


class FakeHTTPClient:
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


def build_client(http_client=None):
    app = FastAPI()
    app.include_router(llm_router)
    app.state.config = CONFIG
    app.state.http_client = http_client or FakeHTTPClient()
    app.state.store = FakeStore()
    app.state.metrics_cache = {}
    app.state.backend_inflight = {}
    app.state.backend_health = {}
    return TestClient(app)


@pytest.fixture
def client():
    return build_client()


@pytest.mark.parametrize(
    "path",
    ["/v1/messages", "/v1/messages/count_tokens", "/tokenize", "/detokenize"],
)
def test_routes_and_rewrites_model_tag(client, path):
    resp = client.post(path, json={"model": "Qwen3-0.6B", "prompt": "hi"})
    assert resp.status_code == 200
    call = client.app.state.http_client.calls[-1]
    assert call["url"] == f"http://localhost:8002{path}"
    assert call["json"]["model"] == "Qwen/Qwen3-0.6B"  # group key -> served tag
    # Logged to the store under the requested group + the upstream path.
    reqs = client.app.state.store.reqs
    assert reqs[-1]["model_key"] == "Qwen3-0.6B"
    assert reqs[-1]["path"] == path
    assert client.app.state.backend_inflight == {}  # accounting balanced


def test_messages_streaming_does_not_inject_stream_options():
    # Anthropic SSE would reject stream_options; the proxy must leave it off.
    chunks = [b'event: message_stop\ndata: {}\n\n']
    http = FakeHTTPClient(SSEResponse(chunks))
    client = build_client(http)
    with client.stream("POST", "/v1/messages", json={"model": "Qwen3-0.6B", "stream": True}) as resp:
        assert resp.status_code == 200
        b"".join(resp.iter_bytes())
    assert "stream_options" not in http.calls[-1]["json"]


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/messages/count_tokens"])
def test_pooling_model_on_messages_is_404(client, path):
    resp = client.post(path, json={"model": "bge-m3", "messages": []})
    assert resp.status_code == 404


@pytest.mark.parametrize("path", ["/tokenize", "/detokenize"])
def test_tokenize_is_kind_agnostic(client, path):
    # A pooling (embed) group still has a tokenizer -> not guarded to chat.
    resp = client.post(path, json={"model": "bge-m3", "prompt": "hi"})
    assert resp.status_code == 200
    call = client.app.state.http_client.calls[-1]
    assert call["url"] == f"http://localhost:8020{path}"


def test_missing_model_is_400(client):
    assert client.post("/v1/messages", json={"messages": []}).status_code == 400


# --- usage normalization (OpenAI passthrough + Anthropic mapping) -------------

def test_normalize_usage_openai_passthrough():
    u = {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
    assert _normalize_usage(u) is u


def test_normalize_usage_anthropic_mapped():
    assert _normalize_usage({"input_tokens": 15, "output_tokens": 40}) == {
        "prompt_tokens": 15, "completion_tokens": 40, "total_tokens": 55,
    }


def test_normalize_usage_count_tokens_only_input():
    # count_tokens has no output -> total falls back to input.
    assert _normalize_usage({"input_tokens": 12}) == {
        "prompt_tokens": 12, "completion_tokens": None, "total_tokens": 12,
    }


def test_normalize_usage_garbage_is_none():
    assert _normalize_usage(None) is None
    assert _normalize_usage({"foo": 1}) is None


def test_usage_from_body_reads_count_tokens_top_level():
    # /v1/messages/count_tokens returns input_tokens at the top level, no `usage`.
    assert _usage_from_body(b'{"input_tokens": 12}') == {"input_tokens": 12}


def test_messages_buffered_logs_anthropic_tokens():
    body = b'{"type":"message","usage":{"input_tokens":15,"output_tokens":40}}'
    http = FakeHTTPClient(FakeResponse(content=body))
    client = build_client(http)
    resp = client.post("/v1/messages", json={"model": "Qwen3-0.6B", "messages": []})
    assert resp.status_code == 200
    req = client.app.state.store.reqs[-1]
    assert (req["prompt_tokens"], req["completion_tokens"], req["total_tokens"]) == (15, 40, 55)


def test_messages_streaming_merges_anthropic_usage():
    # input_tokens arrives on message_start (nested under message), output_tokens
    # on message_delta — the scan must merge both halves.
    chunks = [
        b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":9,"output_tokens":1}}}\n\n',
        b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":10}}\n\n',
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]
    http = FakeHTTPClient(SSEResponse(chunks))
    client = build_client(http)
    with client.stream("POST", "/v1/messages", json={"model": "Qwen3-0.6B", "stream": True}) as resp:
        assert resp.status_code == 200
        b"".join(resp.iter_bytes())
    req = client.app.state.store.reqs[-1]
    assert (req["prompt_tokens"], req["completion_tokens"], req["total_tokens"]) == (9, 10, 19)

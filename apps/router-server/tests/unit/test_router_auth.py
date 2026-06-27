"""API-key enforcement on inference endpoints + usage attribution."""
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.llm_router import auth as auth_mod
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
    def __init__(self):
        self.status_code = 200
        self._content = b'{"ok": true}'
        self.headers = {"content-type": "application/json"}

    async def aread(self):
        return self._content


class FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class FakeHTTPClient:
    def stream(self, method, url, json=None):
        return FakeStreamCtx(FakeResponse())


class FakeStore:
    def __init__(self, keys=None, used_tokens=0):
        self.reqs = []
        self._keys = keys or {}  # hash -> {"id","name"}
        self._used_tokens = used_tokens

    async def record_request(self, **kwargs):
        self.reqs.append(kwargs)

    async def get_active_api_key_by_hash(self, key_hash):
        return self._keys.get(key_hash)

    async def tokens_used_by_key(self, name, since=None):
        return self._used_tokens


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _client(store):
    app = FastAPI()
    app.include_router(llm_router)
    app.state.config = CONFIG
    app.state.http_client = FakeHTTPClient()
    app.state.store = store
    app.state.metrics_cache = {}
    app.state.backend_inflight = {}
    app.state.backend_health = {}
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_cache():
    auth_mod._cache.clear()
    auth_mod._hits.clear()
    auth_mod._quota_cache.clear()
    yield
    auth_mod._cache.clear()
    auth_mod._hits.clear()
    auth_mod._quota_cache.clear()


def test_disabled_by_default_no_key_needed(monkeypatch):
    monkeypatch.delenv("LLMOPS_REQUIRE_API_KEY", raising=False)
    client = _client(FakeStore())
    assert client.post("/v1/chat/completions", json={"model": "Qwen3-0.6B"}).status_code == 200


def test_enabled_rejects_missing_key(monkeypatch):
    monkeypatch.setenv("LLMOPS_REQUIRE_API_KEY", "true")
    client = _client(FakeStore())
    assert client.post("/v1/chat/completions", json={"model": "Qwen3-0.6B"}).status_code == 401


def test_enabled_accepts_admin_token_and_attributes_dashboard(monkeypatch):
    monkeypatch.setenv("LLMOPS_REQUIRE_API_KEY", "true")
    monkeypatch.setenv("LLMOPS_ADMIN_TOKEN", "admintok")
    store = FakeStore()
    client = _client(store)
    r = client.post(
        "/v1/chat/completions", json={"model": "Qwen3-0.6B"},
        headers={"Authorization": "Bearer admintok"},
    )
    assert r.status_code == 200
    assert store.reqs[-1]["api_key_name"] == "dashboard"


def test_enabled_accepts_valid_api_key_and_attributes_name(monkeypatch):
    monkeypatch.setenv("LLMOPS_REQUIRE_API_KEY", "true")
    monkeypatch.delenv("LLMOPS_ADMIN_TOKEN", raising=False)
    store = FakeStore(keys={_hash("sk-llmops-good"): {"id": 7, "name": "ci"}})
    client = _client(store)
    r = client.post(
        "/v1/chat/completions", json={"model": "Qwen3-0.6B"},
        headers={"Authorization": "Bearer sk-llmops-good"},
    )
    assert r.status_code == 200
    assert store.reqs[-1]["api_key_name"] == "ci"


def test_enabled_enforces_rpm_limit(monkeypatch):
    monkeypatch.setenv("LLMOPS_REQUIRE_API_KEY", "true")
    monkeypatch.delenv("LLMOPS_ADMIN_TOKEN", raising=False)
    store = FakeStore(keys={_hash("sk-llmops-lim"): {"id": 9, "name": "ltd", "rpm_limit": 2}})
    client = _client(store)
    h = {"Authorization": "Bearer sk-llmops-lim"}
    body = {"model": "Qwen3-0.6B"}
    assert client.post("/v1/chat/completions", json=body, headers=h).status_code == 200
    assert client.post("/v1/chat/completions", json=body, headers=h).status_code == 200
    assert client.post("/v1/chat/completions", json=body, headers=h).status_code == 429  # over 2/min


def test_enabled_allows_under_token_quota(monkeypatch):
    monkeypatch.setenv("LLMOPS_REQUIRE_API_KEY", "true")
    monkeypatch.delenv("LLMOPS_ADMIN_TOKEN", raising=False)
    store = FakeStore(
        keys={_hash("sk-q"): {"id": 1, "name": "q", "token_quota": 1000, "quota_period": "total"}},
        used_tokens=500,
    )
    client = _client(store)
    r = client.post(
        "/v1/chat/completions", json={"model": "Qwen3-0.6B"},
        headers={"Authorization": "Bearer sk-q"},
    )
    assert r.status_code == 200


def test_enabled_rejects_over_token_quota(monkeypatch):
    monkeypatch.setenv("LLMOPS_REQUIRE_API_KEY", "true")
    monkeypatch.delenv("LLMOPS_ADMIN_TOKEN", raising=False)
    store = FakeStore(
        keys={_hash("sk-q"): {"id": 1, "name": "q", "token_quota": 1000, "quota_period": "daily"}},
        used_tokens=1000,  # already at the cap
    )
    client = _client(store)
    r = client.post(
        "/v1/chat/completions", json={"model": "Qwen3-0.6B"},
        headers={"Authorization": "Bearer sk-q"},
    )
    assert r.status_code == 429


def test_no_quota_means_unlimited(monkeypatch):
    monkeypatch.setenv("LLMOPS_REQUIRE_API_KEY", "true")
    monkeypatch.delenv("LLMOPS_ADMIN_TOKEN", raising=False)
    store = FakeStore(
        keys={_hash("sk-u"): {"id": 2, "name": "u"}},  # no token_quota
        used_tokens=10**9,
    )
    client = _client(store)
    r = client.post(
        "/v1/chat/completions", json={"model": "Qwen3-0.6B"},
        headers={"Authorization": "Bearer sk-u"},
    )
    assert r.status_code == 200


def test_enabled_rejects_unknown_key(monkeypatch):
    monkeypatch.setenv("LLMOPS_REQUIRE_API_KEY", "true")
    client = _client(FakeStore())
    r = client.post(
        "/v1/chat/completions", json={"model": "Qwen3-0.6B"},
        headers={"Authorization": "Bearer sk-llmops-bad"},
    )
    assert r.status_code == 401

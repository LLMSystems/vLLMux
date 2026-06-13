import pytest

from app.routes import config as config_route
from tests.conftest import FAKE_CONFIG

pytestmark = pytest.mark.api


def test_get_config_returns_flattened_summary(client, monkeypatch):
    # Route reads from disk via core.config.load_config; pin it to the fake.
    monkeypatch.setattr(config_route, "load_config", lambda *a, **k: FAKE_CONFIG)

    resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"server", "LLM_engines", "embedding_server"}
    assert "Qwen3-0.6B::qwen3" in body["LLM_engines"]
    assert body["embedding_server"]["port"] == 8005

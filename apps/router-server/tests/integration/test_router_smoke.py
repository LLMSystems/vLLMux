"""Live smoke test against a running router / vLLM server.

Replaces the old assert-less manual script (test/test_router_server.py). It is
marked `integration` and auto-skips unless a server is actually reachable, so it
never breaks a normal `pytest` run.

To run against a local model started via start_llm.sh (port 8000):

    ROUTER_BASE_URL=http://0.0.0.0:8000/v1 pytest -m integration
"""
import os

import pytest

pytestmark = pytest.mark.integration

ROUTER_BASE_URL = os.environ.get("ROUTER_BASE_URL", "http://0.0.0.0:8000/v1")

OpenAI = pytest.importorskip("openai").OpenAI


def _server_up() -> bool:
    import httpx

    try:
        resp = httpx.get(ROUTER_BASE_URL.rstrip("/") + "/models", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = [pytestmark, pytest.mark.skipif(not _server_up(), reason="no live server")]


@pytest.fixture
def client():
    return OpenAI(api_key="EMPTY", base_url=ROUTER_BASE_URL)


def test_models_are_listed(client):
    models = client.models.list()
    assert models.data, "server returned no models"


def test_chat_completion_returns_content(client):
    model_id = client.models.list().data[0].id
    resp = client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": "用一句話介紹你自己"}],
        temperature=0.7,
        max_tokens=16,
    )
    assert resp.choices
    assert resp.choices[0].message.content is not None

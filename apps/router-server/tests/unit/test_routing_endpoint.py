"""GET/POST /routing — read + hot-swap the global routing strategy."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.llm_router.router import router as llm_router
from src.llm_router.routing_strategies import DEFAULT_STRATEGY, STRATEGIES

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(llm_router)
    app.state.routing_strategy = DEFAULT_STRATEGY
    app.state.rr_counters = {}
    return TestClient(app)


def test_get_routing_reports_current_and_catalogue(client):
    r = client.get("/routing")
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"] == DEFAULT_STRATEGY
    assert body["default"] == DEFAULT_STRATEGY
    assert set(body["available"]) == set(STRATEGIES)


def test_post_routing_hot_swaps_and_persists_in_state(client):
    r = client.post("/routing", json={"strategy": "session_affinity"})
    assert r.status_code == 200
    assert r.json()["strategy"] == "session_affinity"
    # State updated, so a subsequent read reflects it.
    assert client.app.state.routing_strategy == "session_affinity"
    assert client.get("/routing").json()["strategy"] == "session_affinity"


def test_post_routing_rejects_unknown_strategy(client):
    r = client.post("/routing", json={"strategy": "bogus"})
    assert r.status_code == 400
    # The global strategy is unchanged after a rejected swap.
    assert client.app.state.routing_strategy == DEFAULT_STRATEGY

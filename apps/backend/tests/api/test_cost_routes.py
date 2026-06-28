"""Cost dashboard routes: summary (open), price CRUD (admin-only)."""
import pytest

pytestmark = pytest.mark.api

ADMIN = {"Authorization": "Bearer secret-admin"}


def _record(store, **kw):
    """Append a request row straight to the in-memory FakeStore (sync)."""
    base = dict(model_key="A", instance_id="a", path="/v1/chat/completions",
                status_code=200, latency_ms=10.0, prompt_tokens=1_000_000,
                completion_tokens=1_000_000, total_tokens=2_000_000,
                api_key_name="k1", ts=0.0)
    base.update(kw)
    store.reqs.append(base)


def test_summary_costs_usage_with_prices(auth_client):
    store = auth_client.app.state.store
    _record(store)
    # Set a price for A: $1/1M in, $2/1M out.
    r = auth_client.put("/api/cost/prices/A",
                        json={"input_price": 1.0, "output_price": 2.0}, headers=ADMIN)
    assert r.status_code == 200

    s = auth_client.get("/api/cost/summary").json()
    a = next(m for m in s["by_model"] if m["model"] == "A")
    assert a["priced"] is True and a["cost"] == 3.0          # 1M*1 + 1M*2
    assert s["total_cost"] == 3.0
    assert s["by_key"][0]["name"] == "k1" and s["by_key"][0]["cost"] == 3.0


def test_unpriced_model_uses_default_and_flags(auth_client):
    store = auth_client.app.state.store
    _record(store, model_key="B")
    s = auth_client.get("/api/cost/summary").json()
    b = next(m for m in s["by_model"] if m["model"] == "B")
    assert b["priced"] is False and b["cost"] == 0.0         # default price 0
    assert s["any_unpriced"] is True


def test_prices_crud_is_admin_only(auth_client):
    # Reading the summary is open (like /usage); editing prices needs admin.
    assert auth_client.get("/api/cost/summary").status_code == 200
    assert auth_client.put("/api/cost/prices/A",
                           json={"input_price": 1.0, "output_price": 1.0}).status_code == 401
    assert auth_client.put("/api/cost/prices/A",
                           json={"input_price": 1.0, "output_price": 1.0},
                           headers=ADMIN).status_code == 200
    listed = auth_client.get("/api/cost/prices", headers=ADMIN).json()
    assert any(p["model"] == "A" for p in listed["prices"])
    assert auth_client.delete("/api/cost/prices/A", headers=ADMIN).status_code == 204
    assert auth_client.delete("/api/cost/prices/A", headers=ADMIN).status_code == 404


def test_price_rejects_negative(auth_client):
    assert auth_client.put("/api/cost/prices/A",
                           json={"input_price": -1.0, "output_price": 1.0},
                           headers=ADMIN).status_code == 422

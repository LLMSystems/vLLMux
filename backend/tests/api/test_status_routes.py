import pytest

pytestmark = pytest.mark.api


def test_status_all_reports_per_instance_health(client):
    resp = client.get("/api/status/all")
    assert resp.status_code == 200
    models = {m["name"]: m for m in resp.json()["models"]}

    # Only port 8002 is "healthy" in the FakeHTTPClient fixture.
    assert models["Qwen3-0.6B::qwen3"]["status"] == "已啟動"
    assert models["Qwen3-0.6B::qwen3-2"]["status"] == "未啟動"
    assert models["Embedding & reranking Server"]["status"] == "未啟動"


def test_starting_models_short_circuits_to_starting(client, app):
    app.state.starting_models = {"Qwen3-0.6B::qwen3-2"}
    resp = client.get("/api/status/all")
    models = {m["name"]: m for m in resp.json()["models"]}
    assert models["Qwen3-0.6B::qwen3-2"]["status"] == "啟動中"

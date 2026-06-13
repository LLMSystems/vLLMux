import pytest

from app.routes import llm as llm_route

pytestmark = pytest.mark.api


def test_start_llm_happy_path(client, monkeypatch):
    called = {}

    def fake_start(app, model_name, config_path):
        called["args"] = (model_name, config_path)

    monkeypatch.setattr(llm_route.model_service, "start_llm", fake_start)

    resp = client.post("/api/llm/start/Qwen3-0.6B::qwen3")
    assert resp.status_code == 200
    assert resp.json()["status"] == "啟動中"
    assert called["args"] == ("Qwen3-0.6B::qwen3", "config.yaml")


def test_start_llm_value_error_is_reported(client, monkeypatch):
    def boom(app, model_name, config_path):
        raise ValueError("模型群組未在配置中找到")

    monkeypatch.setattr(llm_route.model_service, "start_llm", boom)

    resp = client.post("/api/llm/start/does-not-exist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "錯誤"
    assert "未在配置中找到" in body["message"]


def test_stop_llm_happy_path(client, monkeypatch):
    called = {}
    monkeypatch.setattr(
        llm_route.model_service,
        "stop_llm",
        lambda app, model_name: called.setdefault("name", model_name),
    )

    resp = client.post("/api/llm/stop/Qwen3-0.6B::qwen3")
    assert resp.status_code == 200
    assert resp.json()["status"] == "未啟動"
    assert called["name"] == "Qwen3-0.6B::qwen3"

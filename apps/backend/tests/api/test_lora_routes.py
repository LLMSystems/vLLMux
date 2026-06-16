import json

import pytest

pytestmark = pytest.mark.api


def _make_adapter(root, name, base="Qwen/Qwen3-0.6B", r=8):
    d = root / name
    d.mkdir(parents=True)
    (d / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": base, "r": r,
                    "target_modules": ["q_proj", "v_proj"]})
    )
    (d / "adapter_model.safetensors").write_bytes(b"\0" * 512)


def test_list_lora_returns_adapters_and_disk(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LLMOPS_LORA_DIR", str(tmp_path))
    _make_adapter(tmp_path, "sql-lora", base="meta/Llama", r=16)

    r = client.get("/api/lora")
    assert r.status_code == 200
    body = r.json()
    assert body["root"] == str(tmp_path)
    assert "disk" in body and "free" in body["disk"]
    names = [a["name"] for a in body["adapters"]]
    assert names == ["sql-lora"]
    assert body["adapters"][0]["base_model"] == "meta/Llama"
    assert body["adapters"][0]["rank"] == 16


def test_delete_lora_requires_admin(auth_client, tmp_path, monkeypatch):
    monkeypatch.setenv("LLMOPS_LORA_DIR", str(tmp_path))
    _make_adapter(tmp_path, "gone")
    # admin gating on (token = secret-admin); no header -> 401
    assert auth_client.delete("/api/lora/gone").status_code == 401


def test_delete_lora_removes(auth_client, tmp_path, monkeypatch):
    monkeypatch.setenv("LLMOPS_LORA_DIR", str(tmp_path))
    _make_adapter(tmp_path, "gone")
    r = auth_client.delete("/api/lora/gone", headers={"X-Admin-Token": "secret-admin"})
    assert r.status_code == 204
    assert auth_client.delete("/api/lora/gone", headers={"X-Admin-Token": "secret-admin"}).status_code == 404


def test_list_lora_downloads_empty(client):
    assert client.get("/api/lora/downloads").json() == []

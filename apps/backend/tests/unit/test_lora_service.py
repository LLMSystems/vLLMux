import json

import pytest

from app.services import lora_service

pytestmark = pytest.mark.unit


def _make_adapter(root, name, base="Qwen/Qwen3-0.6B", r=8):
    d = root / name
    d.mkdir(parents=True)
    (d / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": base, "r": r, "lora_alpha": 16,
                    "target_modules": ["q_proj", "v_proj"]})
    )
    (d / "adapter_model.safetensors").write_bytes(b"\0" * 1024)
    return d


def test_scan_parses_base_and_rank(tmp_path, monkeypatch):
    monkeypatch.setenv("LLMOPS_LORA_DIR", str(tmp_path))
    _make_adapter(tmp_path, "sql-lora", base="meta/Llama", r=16)
    (tmp_path / "not-an-adapter").mkdir()  # no adapter_config.json -> ignored

    out = lora_service.scan()
    assert [a["name"] for a in out] == ["sql-lora"]
    a = out[0]
    assert a["base_model"] == "meta/Llama"
    assert a["rank"] == 16
    assert a["target_modules"] == ["q_proj", "v_proj"]
    assert a["size_on_disk"] > 0
    assert a["path"].endswith("/sql-lora")


def test_scan_lists_loose_gguf_adapters(tmp_path, monkeypatch):
    # llama.cpp GGUF LoRA: a loose *.gguf file (not a folder) is a valid adapter.
    monkeypatch.setenv("LLMOPS_LORA_DIR", str(tmp_path))
    _make_adapter(tmp_path, "peft-lora")                       # PEFT folder
    (tmp_path / "qwen3-test-lora-f16.gguf").write_bytes(b"GGUF" + b"\0" * 100)
    (tmp_path / "notes.txt").write_text("ignored")            # non-.gguf file -> skipped

    out = {a["name"]: a for a in lora_service.scan()}
    assert set(out) == {"peft-lora", "qwen3-test-lora-f16.gguf"}
    assert out["peft-lora"]["format"] == "peft"
    g = out["qwen3-test-lora-f16.gguf"]
    assert g["format"] == "gguf" and g["base_model"] is None
    assert g["path"].endswith("/qwen3-test-lora-f16.gguf") and g["size_on_disk"] > 0


def test_delete_removes_loose_gguf(tmp_path, monkeypatch):
    monkeypatch.setenv("LLMOPS_LORA_DIR", str(tmp_path))
    (tmp_path / "adapter.gguf").write_bytes(b"GGUF")
    assert lora_service.delete("adapter.gguf") is True
    assert not (tmp_path / "adapter.gguf").exists()


def test_scan_empty_when_root_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LLMOPS_LORA_DIR", str(tmp_path / "nope"))
    assert lora_service.scan() == []


def test_delete_removes_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LLMOPS_LORA_DIR", str(tmp_path))
    _make_adapter(tmp_path, "gone")
    assert lora_service.delete("gone") is True
    assert lora_service.delete("gone") is False  # already gone


def test_delete_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("LLMOPS_LORA_DIR", str(tmp_path))
    for bad in ("../etc", "a/b", "..", ""):
        with pytest.raises(ValueError):
            lora_service.delete(bad)

"""Audit body redaction + target extraction (pure helpers)."""
import json

import pytest

from app.core.audit import extract_target, redact_body

pytestmark = pytest.mark.unit


def test_extract_target_from_path():
    assert extract_target("/api/models/Qwen3-0.6B::qwen3/start") == "Qwen3-0.6B::qwen3"
    assert extract_target("/api/models/Qwen3-0.6B/autoscale") == "Qwen3-0.6B"
    assert extract_target("/api/keys/3") == "3"
    assert extract_target("/api/models") is None
    assert extract_target("/healthz") is None


def test_redact_scrubs_secret_fields():
    body = json.dumps({
        "label": "alice",
        "token": "sk-op-supersecret",
        "nested": {"password": "hunter2", "ok": 1},
        "list": [{"api_key": "leak"}, {"keep": "me"}],
    }).encode()
    out = json.loads(redact_body(body, "application/json"))
    assert out["label"] == "alice"
    assert out["token"] == "***redacted***"
    assert out["nested"]["password"] == "***redacted***"
    assert out["nested"]["ok"] == 1
    assert out["list"][0]["api_key"] == "***redacted***"
    assert out["list"][1]["keep"] == "me"


def test_redact_ignores_non_json_and_empty():
    assert redact_body(b"", "application/json") is None
    assert redact_body(b"hello", "text/plain") is None
    assert redact_body(b"not json", "application/json") is None


def test_redact_caps_large_body():
    big = json.dumps({"x": "a" * 9000}).encode()
    out = json.loads(redact_body(big, "application/json"))
    assert out["_note"] == "body too large to audit"

"""Admin-token gating on write routes + API-key management."""
import pytest

pytestmark = pytest.mark.api

KEY = "Qwen3-0.6B::qwen3"
TOKEN = "secret-admin"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


def test_auth_status_disabled_by_default(client):
    assert client.get("/api/auth/status").json() == {"auth_enabled": False, "sso_enabled": False}


def test_auth_status_enabled_when_token_set(auth_client):
    assert auth_client.get("/api/auth/status").json() == {"auth_enabled": True, "sso_enabled": False}


def test_writes_open_when_auth_disabled(client):
    assert client.post(f"/api/models/{KEY}/start").status_code == 202


def test_write_requires_token_when_enabled(auth_client):
    assert auth_client.post(f"/api/models/{KEY}/start").status_code == 401
    assert auth_client.post(f"/api/models/{KEY}/start", headers=BEARER).status_code == 202


def test_write_rejects_bad_token(auth_client):
    assert auth_client.post(f"/api/models/{KEY}/stop", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_reads_stay_open_when_enabled(auth_client):
    assert auth_client.get("/api/models").status_code == 200
    assert auth_client.get(f"/api/models/{KEY}").status_code == 200


def test_auth_verify(auth_client):
    assert auth_client.post("/api/auth/verify").status_code == 401
    assert auth_client.post("/api/auth/verify", headers=BEARER).json() == {"ok": True}


def test_keys_crud(auth_client):
    created = auth_client.post("/api/keys", json={"name": "ci"}, headers=BEARER)
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("sk-llmops-")  # plaintext returned once
    kid = body["id"]

    listed = auth_client.get("/api/keys", headers=BEARER).json()
    row = next(k for k in listed if k["id"] == kid)
    assert "key" not in row and "key_hash" not in row  # never leak the secret
    assert row["prefix"].startswith("sk-llmops-")

    assert auth_client.delete(f"/api/keys/{kid}", headers=BEARER).status_code == 204
    assert auth_client.delete(f"/api/keys/{kid}", headers=BEARER).status_code == 404  # already gone


def test_keys_require_admin(auth_client):
    assert auth_client.post("/api/keys", json={"name": "x"}).status_code == 401
    assert auth_client.get("/api/keys").status_code == 401

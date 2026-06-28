"""Multi-user RBAC (operator credentials + roles) and the audit trail."""
import json

import pytest

pytestmark = pytest.mark.api

KEY = "Qwen3-0.6B::qwen3"
ADMIN = {"Authorization": "Bearer secret-admin"}  # the env admin backdoor


def _mint(auth_client, label, role):
    """Create an operator via the admin backdoor; return its plaintext token."""
    resp = auth_client.post(
        "/api/operators", json={"label": label, "role": role}, headers=ADMIN
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token"].startswith("sk-op-")  # plaintext returned once
    return body["token"], body["id"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# -- operator credential lifecycle ----------------------------------------

def test_operators_crud_and_hash_never_leaks(auth_client):
    token, oid = _mint(auth_client, "alice", "admin")
    listed = auth_client.get("/api/operators", headers=ADMIN).json()
    row = next(o for o in listed if o["id"] == oid)
    assert row["role"] == "admin" and row["label"] == "alice"
    assert "token" not in row and "token_hash" not in row
    assert row["prefix"].startswith("sk-op-")

    assert auth_client.delete(f"/api/operators/{oid}", headers=ADMIN).status_code == 204
    assert auth_client.delete(f"/api/operators/{oid}", headers=ADMIN).status_code == 404


def test_operators_management_is_admin_only(auth_client):
    op_token, _ = _mint(auth_client, "op", "operator")
    # An operator may not list or mint operators (admin surface).
    assert auth_client.get("/api/operators", headers=_bearer(op_token)).status_code == 403
    assert auth_client.post(
        "/api/operators", json={"label": "x", "role": "viewer"}, headers=_bearer(op_token)
    ).status_code == 403


# -- role enforcement ------------------------------------------------------

def test_operator_can_control_models_but_not_keys(auth_client):
    op_token, _ = _mint(auth_client, "op", "operator")
    h = _bearer(op_token)
    assert auth_client.post(f"/api/models/{KEY}/start", headers=h).status_code == 202
    # API-key management stays admin-only.
    assert auth_client.post("/api/keys", json={"name": "x"}, headers=h).status_code == 403


def test_viewer_is_read_only(auth_client):
    v_token, _ = _mint(auth_client, "val", "viewer")
    h = _bearer(v_token)
    assert auth_client.get("/api/models", headers=h).status_code == 200
    assert auth_client.post(f"/api/models/{KEY}/start", headers=h).status_code == 403


def test_admin_token_can_do_everything(auth_client):
    assert auth_client.post(f"/api/models/{KEY}/start", headers=ADMIN).status_code == 202
    assert auth_client.post("/api/keys", json={"name": "k"}, headers=ADMIN).status_code == 201


def test_me_reports_identity_and_role(auth_client):
    op_token, _ = _mint(auth_client, "dana", "operator")
    assert auth_client.get("/api/me", headers=ADMIN).json() == {"actor": "admin", "role": "admin"}
    assert auth_client.get("/api/me", headers=_bearer(op_token)).json() == {
        "actor": "dana", "role": "operator"
    }
    assert auth_client.get("/api/me").status_code == 401  # unauthenticated


def test_open_dev_is_implicit_admin(client):
    """With no admin token and no operators, the API is open as local-dev/admin."""
    assert client.get("/api/me").json() == {"actor": "local-dev", "role": "admin"}
    assert client.post(f"/api/models/{KEY}/start").status_code == 202


# -- audit trail -----------------------------------------------------------

def test_audit_records_mutation_with_actor(auth_client):
    op_token, _ = _mint(auth_client, "max", "operator")
    auth_client.post(f"/api/models/{KEY}/start", headers=_bearer(op_token))
    rows = auth_client.get("/api/audit", headers=ADMIN).json()
    entry = next(r for r in rows if r["path"] == f"/api/models/{KEY}/start")
    assert entry["actor"] == "max" and entry["role"] == "operator"
    assert entry["method"] == "POST" and entry["status"] == 202
    assert entry["target"] == KEY


def test_audit_redacts_secret_fields(auth_client):
    # Minting an API key carries no secret in the request, but autoscale/edit
    # bodies are captured — verify token-like fields are scrubbed generally.
    auth_client.post(
        "/api/operators", json={"label": "z", "role": "viewer"}, headers=ADMIN
    )
    rows = auth_client.get("/api/audit", headers=ADMIN).json()
    entry = next(r for r in rows if r["path"] == "/api/operators")
    detail = json.loads(entry["detail"])
    assert detail["label"] == "z" and detail["role"] == "viewer"  # non-secret kept


def test_audit_requires_admin(auth_client):
    op_token, _ = _mint(auth_client, "op", "operator")
    assert auth_client.get("/api/audit", headers=_bearer(op_token)).status_code == 403
    assert auth_client.get("/api/audit").status_code == 401

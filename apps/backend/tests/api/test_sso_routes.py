"""SSO routes: login redirect (PKCE/state), callback (mocked IdP) and the session
cookie resolving an identity through resolve_actor."""
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from app.core import oidc
from app.core.settings import BackendSettings

pytestmark = pytest.mark.api

SSO = dict(
    admin_token="secret-admin",  # auth_enabled so the open-dev short-circuit is off
    oidc_issuer="https://idp.example",
    oidc_client_id="client-123",
    oidc_client_secret="shh",
    oidc_operator_groups=("oncall",),
    oidc_admin_emails=("boss@corp.com",),
    oidc_default_role="",  # deny unmatched, so role mapping is exercised
    session_secret="a-long-enough-session-secret-value-123",
)

DISCOVERY = {
    "authorization_endpoint": "https://idp.example/authorize",
    "token_endpoint": "https://idp.example/token",
    "jwks_uri": "https://idp.example/jwks",
}


@pytest.fixture
def sso_app(app, monkeypatch):
    app.state.settings = BackendSettings(**SSO)

    async def fake_discovery(settings, http_client):
        return DISCOVERY

    monkeypatch.setattr(oidc, "get_discovery", fake_discovery)
    return app


def test_login_redirects_to_idp_with_pkce_and_sets_tx(sso_app):
    client = TestClient(sso_app)
    r = client.get("/api/auth/sso/login?next=/cost", follow_redirects=False)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://idp.example/authorize?")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    assert q["client_id"] == ["client-123"]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"]
    assert q["redirect_uri"][0].endswith("/api/auth/sso/callback")
    assert oidc.TX_COOKIE in r.cookies  # signed transaction stashed


def test_login_404_when_sso_disabled(app):
    app.state.settings = BackendSettings(admin_token="x")  # no OIDC config
    assert TestClient(app).get("/api/auth/sso/login").status_code == 404


def _begin_login(client):
    """Run /login and return (state, tx_cookie) for crafting a callback."""
    r = client.get("/api/auth/sso/login?next=/cost", follow_redirects=False)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    return q["state"][0], r.cookies[oidc.TX_COOKIE]


def test_callback_sets_session_and_redirects(sso_app, monkeypatch):
    client = TestClient(sso_app)
    state, tx = _begin_login(client)

    async def fake_exchange(*a, **k):
        return {"id_token": "fake"}

    async def fake_verify(id_token, settings, discovery, *, nonce):
        return {"sub": "u1", "email": "x@corp.com", "name": "X", "groups": ["oncall"]}

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc, "verify_id_token", fake_verify)

    client.cookies.set(oidc.TX_COOKIE, tx)
    r = client.get(f"/api/auth/sso/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/cost"
    assert oidc.SESSION_COOKIE in r.cookies

    # The session cookie now resolves an identity (operator from the group) — no
    # bearer token needed.
    me = client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["actor"] == "x@corp.com" and body["role"] == "operator"


def test_callback_rejects_state_mismatch(sso_app):
    client = TestClient(sso_app)
    _state, tx = _begin_login(client)
    client.cookies.set(oidc.TX_COOKIE, tx)
    r = client.get("/api/auth/sso/callback?code=abc&state=WRONG", follow_redirects=False)
    assert r.status_code == 400


def test_callback_403_when_no_role(sso_app, monkeypatch):
    client = TestClient(sso_app)
    state, tx = _begin_login(client)

    async def fake_exchange(*a, **k):
        return {"id_token": "fake"}

    async def fake_verify(*a, **k):
        return {"sub": "u2", "email": "stranger@elsewhere.com", "groups": []}

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc, "verify_id_token", fake_verify)
    client.cookies.set(oidc.TX_COOKIE, tx)
    r = client.get(f"/api/auth/sso/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 403  # default_role empty -> deny


def test_logout_clears_session(sso_app):
    client = TestClient(sso_app)
    r = client.post("/api/auth/sso/logout")
    assert r.status_code == 200 and r.json()["ok"] is True

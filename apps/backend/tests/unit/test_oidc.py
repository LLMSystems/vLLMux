"""OIDC helpers: role mapping, signed session/tx cookies, PKCE, safe redirect."""
import time

import pytest

from app.core.auth import Role
from app.core.oidc import (
    generate_pkce,
    make_session,
    make_tx,
    map_role,
    read_session,
    read_tx,
    safe_next,
)
from app.core.settings import BackendSettings

pytestmark = pytest.mark.unit


def _s(**kw):
    base = dict(oidc_issuer="https://idp", oidc_client_id="c", oidc_client_secret="x",
                session_secret="a-long-enough-session-secret-value-123")
    base.update(kw)
    return BackendSettings(**base)


# ---- role mapping --------------------------------------------------------

def test_admin_email_wins():
    s = _s(oidc_admin_emails=("boss@corp.com",), oidc_viewer_groups=("everyone",))
    assert map_role("BOSS@corp.com", ["everyone"], s) == Role.ADMIN  # case-insensitive


def test_group_precedence_admin_over_operator():
    s = _s(oidc_admin_groups=("platform",), oidc_operator_groups=("oncall",))
    assert map_role("x@c.com", ["oncall", "platform"], s) == Role.ADMIN
    assert map_role("x@c.com", ["oncall"], s) == Role.OPERATOR


def test_default_role_when_no_match():
    assert map_role("x@c.com", [], _s(oidc_default_role="viewer")) == Role.VIEWER


def test_empty_default_denies():
    assert map_role("x@c.com", ["nope"], _s(oidc_default_role="")) is None


# ---- session cookie ------------------------------------------------------

def test_session_round_trips():
    s = _s()
    tok = make_session({"sub": "1", "email": "a@c.com", "name": "A", "role": "operator"}, s)
    claims = read_session(tok, s)
    assert claims["role"] == "operator" and claims["email"] == "a@c.com"


def test_session_rejects_wrong_secret():
    tok = make_session({"sub": "1", "role": "admin"}, _s(session_secret="secret-one-aaaaaaaaaaaaaaaa"))
    assert read_session(tok, _s(session_secret="secret-two-bbbbbbbbbbbbbbbb")) is None


def test_session_expired_is_rejected():
    s = _s(session_ttl=-1)  # already expired
    assert read_session(make_session({"sub": "1", "role": "admin"}, s), s) is None


def test_read_session_none_and_garbage():
    s = _s()
    assert read_session(None, s) is None
    assert read_session("not-a-jwt", s) is None


# ---- tx cookie + pkce + redirect ----------------------------------------

def test_tx_round_trips():
    s = _s()
    tx = make_tx({"state": "st", "nonce": "no", "verifier": "v", "next": "/cost"}, s)
    out = read_tx(tx, s)
    assert out["state"] == "st" and out["next"] == "/cost"


def test_pkce_challenge_is_url_safe_no_padding():
    verifier, challenge = generate_pkce()
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge
    assert verifier and challenge != verifier


def test_safe_next_blocks_open_redirect():
    assert safe_next("/cost") == "/cost"
    assert safe_next("//evil.com") == "/"
    assert safe_next("https://evil.com") == "/"
    assert safe_next(None) == "/"

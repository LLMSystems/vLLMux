"""OIDC (Authorization Code + PKCE) SSO — manual, dependency-light.

Uses httpx for discovery + token exchange and PyJWT for id_token (RS256/JWKS)
and our own session/state cookies (HS256). No authlib, no server-side session
store: state/nonce/PKCE and the post-login session all live in short, signed
cookies. Everything an IdP isn't needed for (role mapping, cookie sign/verify) is
a pure function, so it's unit-testable without a live provider.

See docs/sso-design_zh-CN.md.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from urllib.parse import urlencode

import jwt

from app.core.auth import Role
from app.core.settings import BackendSettings

logger = logging.getLogger("llmops.oidc")

SESSION_COOKIE = "llmops_session"
TX_COOKIE = "llmops_sso_tx"
_TX_TTL = 300  # 5 min for the login round-trip

# Cached discovery documents, keyed by issuer (TTL'd).
_DISCOVERY: dict[str, tuple[float, dict]] = {}
_DISCOVERY_TTL = 3600.0


# ---- role mapping (pure) --------------------------------------------------

def map_role(email: str | None, groups, settings: BackendSettings) -> Role | None:
    """Map IdP identity to a Role: admin email > group match > default role.
    Returns None when nothing matches and there is no default (deny)."""
    if email and email.lower() in {e.lower() for e in settings.oidc_admin_emails}:
        return Role.ADMIN
    gset = {str(g) for g in (groups or [])}
    if gset & set(settings.oidc_admin_groups):
        return Role.ADMIN
    if gset & set(settings.oidc_operator_groups):
        return Role.OPERATOR
    if gset & set(settings.oidc_viewer_groups):
        return Role.VIEWER
    default = (settings.oidc_default_role or "").strip().lower()
    if default in (r.value for r in Role):
        return Role(default)
    return None


# ---- signed cookies (HS256, our own secret) -------------------------------

def make_session(claims: dict, settings: BackendSettings) -> str:
    now = int(time.time())
    payload = {
        "sub": claims.get("sub"),
        "email": claims.get("email"),
        "name": claims.get("name") or claims.get("email") or claims.get("sub"),
        "role": claims["role"],
        "iat": now,
        "exp": now + int(settings.session_ttl),
    }
    return jwt.encode(payload, settings.signing_secret, algorithm="HS256")


def read_session(token: str | None, settings: BackendSettings) -> dict | None:
    if not token:
        return None
    try:
        return jwt.decode(token, settings.signing_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def make_tx(data: dict, settings: BackendSettings) -> str:
    return jwt.encode(
        {**data, "exp": int(time.time()) + _TX_TTL}, settings.signing_secret, algorithm="HS256"
    )


def read_tx(token: str | None, settings: BackendSettings) -> dict | None:
    if not token:
        return None
    try:
        return jwt.decode(token, settings.signing_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ---- PKCE + safe redirect helpers -----------------------------------------

def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def safe_next(target: str | None) -> str:
    """Only allow same-site absolute paths, to block open redirects."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


def redirect_uri(request, settings: BackendSettings) -> str:
    if settings.oidc_redirect_url:
        return settings.oidc_redirect_url
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}/api/auth/sso/callback"


def is_https(request) -> bool:
    return (request.headers.get("x-forwarded-proto", request.url.scheme)) == "https"


# ---- IdP interaction ------------------------------------------------------

async def get_discovery(settings: BackendSettings, http_client) -> dict:
    """Fetch + cache the OIDC discovery document for the configured issuer."""
    iss = settings.oidc_issuer
    cached = _DISCOVERY.get(iss)
    if cached and (time.time() - cached[0]) < _DISCOVERY_TTL:
        return cached[1]
    url = f"{iss}/.well-known/openid-configuration"
    resp = await http_client.get(url, timeout=10.0)
    resp.raise_for_status()
    doc = resp.json()
    _DISCOVERY[iss] = (time.time(), doc)
    return doc


def build_authorize_url(settings: BackendSettings, discovery: dict, *,
                        state: str, nonce: str, challenge: str, redirect: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect,
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{discovery['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(settings: BackendSettings, discovery: dict, http_client, *,
                        code: str, verifier: str, redirect: str) -> dict:
    resp = await http_client.post(
        discovery["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
            "code_verifier": verifier,
        },
        headers={"Accept": "application/json"},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


async def verify_id_token(id_token: str, settings: BackendSettings, discovery: dict,
                          *, nonce: str | None) -> dict:
    """Verify the id_token's signature (JWKS) + iss/aud/exp/nonce; return claims.

    The JWKS fetch in PyJWKClient is blocking, so it runs in the default executor.
    """
    def _decode() -> dict:
        client = jwt.PyJWKClient(discovery["jwks_uri"])
        signing_key = client.get_signing_key_from_jwt(id_token)
        return jwt.decode(
            id_token, signing_key.key, algorithms=["RS256", "ES256"],
            audience=settings.oidc_client_id, issuer=settings.oidc_issuer,
        )

    claims = await asyncio.get_event_loop().run_in_executor(None, _decode)
    if nonce is not None and claims.get("nonce") != nonce:
        raise ValueError("nonce mismatch")
    return claims

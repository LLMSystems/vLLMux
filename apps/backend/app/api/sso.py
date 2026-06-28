"""SSO (OIDC) endpoints: login redirect, callback, logout.

These mint the session — they are deliberately unauthenticated. The heavy lifting
(PKCE, id_token verification, role mapping, cookie signing) lives in
app.core.oidc; here we just sequence the redirect round-trip and set cookies.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.core import oidc

logger = logging.getLogger("llmops.sso")

router = APIRouter(prefix="/auth/sso", tags=["sso"])


def _require_sso(request: Request):
    settings = request.app.state.settings
    if not settings.sso_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO is not configured")
    return settings


def _set_cookie(resp, key, value, request, *, max_age):
    resp.set_cookie(
        key, value, max_age=max_age, httponly=True, samesite="lax",
        secure=oidc.is_https(request), path="/",
    )


def _norm_groups(raw):
    if isinstance(raw, str):
        return [g for g in raw.replace(",", " ").split() if g]
    if isinstance(raw, list):
        return raw
    return []


@router.get("/login")
async def sso_login(request: Request):
    settings = _require_sso(request)
    http_client = request.app.state.http_client
    try:
        discovery = await oidc.get_discovery(settings, http_client)
    except Exception as e:  # discovery unreachable -> can't start login
        logger.warning("OIDC discovery failed: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "OIDC provider unreachable")

    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = oidc.generate_pkce()
    redirect = oidc.redirect_uri(request, settings)
    tx = oidc.make_tx(
        {"state": state, "nonce": nonce, "verifier": verifier,
         "next": oidc.safe_next(request.query_params.get("next")), "redirect": redirect},
        settings,
    )
    url = oidc.build_authorize_url(
        settings, discovery, state=state, nonce=nonce, challenge=challenge, redirect=redirect
    )
    resp = RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    _set_cookie(resp, oidc.TX_COOKIE, tx, request, max_age=oidc._TX_TTL)
    return resp


@router.get("/callback")
async def sso_callback(request: Request):
    settings = _require_sso(request)
    params = request.query_params
    if params.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"SSO error: {params.get('error_description') or params['error']}")

    tx = oidc.read_tx(request.cookies.get(oidc.TX_COOKIE), settings)
    if not tx:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing or expired login transaction")
    if not params.get("state") or not secrets.compare_digest(params["state"], tx["state"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "state mismatch")
    code = params.get("code")
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing authorization code")

    http_client = request.app.state.http_client
    discovery = await oidc.get_discovery(settings, http_client)
    try:
        tokens = await oidc.exchange_code(
            settings, discovery, http_client,
            code=code, verifier=tx["verifier"], redirect=tx["redirect"],
        )
        claims = await oidc.verify_id_token(
            tokens["id_token"], settings, discovery, nonce=tx["nonce"]
        )
    except Exception as e:
        logger.warning("SSO callback token/verify failed: %s", e)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "SSO token exchange/verification failed")

    role = oidc.map_role(claims.get("email"),
                         _norm_groups(claims.get(settings.oidc_groups_claim)), settings)
    if role is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no role for this account")

    session = oidc.make_session(
        {"sub": claims.get("sub"), "email": claims.get("email"),
         "name": claims.get("name"), "role": role.value},
        settings,
    )
    resp = RedirectResponse(oidc.safe_next(tx.get("next")), status_code=status.HTTP_302_FOUND)
    _set_cookie(resp, oidc.SESSION_COOKIE, session, request, max_age=settings.session_ttl)
    resp.delete_cookie(oidc.TX_COOKIE, path="/")
    return resp


@router.post("/logout")
async def sso_logout(request: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(oidc.SESSION_COOKIE, path="/")
    return resp

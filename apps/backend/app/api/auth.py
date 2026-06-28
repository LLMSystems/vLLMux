"""Admin auth status + API-key management (all key mutations are admin-gated)."""
from __future__ import annotations

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.auth import (
    Role,
    generate_api_key,
    generate_operator_token,
    require_admin,
    require_viewer,
    resolve_actor,
)

router = APIRouter(tags=["auth"])

QuotaPeriod = Literal["total", "daily", "monthly"]


def _store(request: Request):
    return request.app.state.store


def _period_start(quota_period: str | None) -> float | None:
    """Start of the current quota window (UTC epoch seconds); None == all time."""
    if quota_period == "daily":
        now = datetime.datetime.now(datetime.timezone.utc)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if quota_period == "monthly":
        now = datetime.datetime.now(datetime.timezone.utc)
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return None


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    rpm_limit: int | None = Field(default=None, ge=1)  # requests/min; None = unlimited
    token_quota: int | None = Field(default=None, ge=1)  # max tokens/period; None = unlimited
    quota_period: QuotaPeriod = "total"


@router.get("/auth/status")
async def auth_status(request: Request):
    """Public: lets the UI decide whether to prompt for a token / offer SSO."""
    settings = request.app.state.settings
    return {"auth_enabled": settings.auth_enabled, "sso_enabled": settings.sso_enabled}


@router.post("/auth/verify", dependencies=[Depends(require_admin)])
async def auth_verify():
    """200 iff the supplied admin token is valid (or auth is disabled)."""
    return {"ok": True}


@router.get("/keys", dependencies=[Depends(require_admin)])
async def list_keys(request: Request):
    store = _store(request)
    keys = await store.list_api_keys()
    usage = await store.api_key_usage()
    for k in keys:
        u = usage.get(k["name"], {})
        k["request_count"] = u.get("request_count", 0)
        k["total_tokens"] = u.get("total_tokens", 0)
        k["usage_last_ts"] = u.get("last_ts")
        # Tokens consumed in the current quota window (for used/remaining display).
        if k.get("token_quota"):
            k["quota_used"] = await store.tokens_used_by_key(
                k["name"], _period_start(k.get("quota_period"))
            )
        else:
            k["quota_used"] = None
    return keys


@router.post("/keys", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_key(body: CreateKeyRequest, request: Request):
    """Mint a key. The plaintext is returned **once** — it is never stored."""
    plaintext, key_hash, prefix = generate_api_key()
    key_id = await _store(request).create_api_key(
        body.name.strip(), key_hash, prefix, rpm_limit=body.rpm_limit,
        token_quota=body.token_quota,
        quota_period=body.quota_period if body.token_quota else None,
    )
    return {"id": key_id, "name": body.name.strip(), "prefix": prefix, "key": plaintext}


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def revoke_key(key_id: int, request: Request):
    if not await _store(request).revoke_api_key(key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown or already-revoked key: {key_id}")


# ---- Operators (control-plane users) -------------------------------------

class CreateOperatorRequest(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    role: Role = Role.OPERATOR


class UpdateOperatorRequest(BaseModel):
    role: Role


@router.get("/me")
async def whoami(request: Request, _: None = Depends(require_viewer)):
    """The caller's resolved identity + role (drives the UI's chrome/permissions)."""
    actor, role = await resolve_actor(request)
    return {"actor": actor, "role": role.value if role else None}


@router.get("/operators", dependencies=[Depends(require_admin)])
async def list_operators(request: Request):
    return await _store(request).list_operators()


@router.post("/operators", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
async def create_operator(body: CreateOperatorRequest, request: Request):
    """Mint an operator token. The plaintext is returned **once** — never stored."""
    plaintext, token_hash, prefix = generate_operator_token()
    op_id = await _store(request).create_operator(
        body.label.strip(), token_hash, prefix, body.role.value
    )
    return {
        "id": op_id, "label": body.label.strip(), "role": body.role.value,
        "prefix": prefix, "token": plaintext,
    }


@router.patch("/operators/{operator_id}", dependencies=[Depends(require_admin)])
async def update_operator(operator_id: int, body: UpdateOperatorRequest, request: Request):
    """Change an operator's role (takes effect immediately, incl. the router)."""
    if not await _store(request).set_operator_role(operator_id, body.role.value):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"unknown or revoked operator: {operator_id}"
        )
    return {"id": operator_id, "role": body.role.value}


@router.post("/operators/{operator_id}/rotate", dependencies=[Depends(require_admin)])
async def rotate_operator(operator_id: int, request: Request):
    """Issue a fresh token for an operator; the old one stops working at once.
    The new plaintext is returned **once**."""
    plaintext, token_hash, prefix = generate_operator_token()
    if not await _store(request).rotate_operator_token(operator_id, token_hash, prefix):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"unknown or revoked operator: {operator_id}"
        )
    return {"id": operator_id, "prefix": prefix, "token": plaintext}


@router.delete("/operators/{operator_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def revoke_operator(operator_id: int, request: Request):
    if not await _store(request).revoke_operator(operator_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"unknown or already-revoked operator: {operator_id}"
        )


# ---- Audit log -----------------------------------------------------------

@router.get("/audit", dependencies=[Depends(require_admin)])
async def list_audit(
    request: Request,
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    since: float | None = None,
    until: float | None = None,
    before: int | None = None,
    limit: int = 200,
):
    """Control-plane change history (newest first), with optional filters.
    ``before`` is an id cursor for pagination (rows older than that id)."""
    return await _store(request).list_audit(
        actor=actor, action=action, target=target, since=since, until=until,
        before=before, limit=limit,
    )

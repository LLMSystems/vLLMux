"""Authentication & authorization for the control API.

Three credential types (see docs/rbac-audit-design_zh-CN.md):
  * the **admin token** (a shared env secret) — the always-present rescue/bootstrap
    identity, treated as role ``admin``;
  * **operator credentials** (generated here, stored hashed) — named control-plane
    users, each carrying a role (viewer / operator / admin);
  * **API keys** (managed elsewhere) authenticate router inference, not this API.

Only the SHA-256 hash of any token is ever persisted — the plaintext is shown to
the operator exactly once, at creation.

Authorization is role-based. Roles are monotonic: ``admin ⊃ operator ⊃ viewer``,
so a guard only needs to compare against a minimum role. ``resolve_actor`` maps a
request to (actor, role) with backward-compatible fallback rules so existing
single-admin-token deployments and open local-dev keep working unchanged.
"""
from __future__ import annotations

import hashlib
import secrets
from enum import Enum

from fastapi import Header, HTTPException, Request, status

KEY_PREFIX = "sk-llmops-"
OPERATOR_PREFIX = "sk-op-"


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


# Higher = more privilege. A guard passes when caller rank >= the required rank.
_RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, key_hash, display_prefix) for a fresh API key."""
    plaintext = KEY_PREFIX + secrets.token_urlsafe(24)
    display = f"{plaintext[: len(KEY_PREFIX) + 4]}…{plaintext[-4:]}"
    return plaintext, hash_key(plaintext), display


def generate_operator_token() -> tuple[str, str, str]:
    """Return (plaintext, token_hash, display_prefix) for a fresh operator token."""
    plaintext = OPERATOR_PREFIX + secrets.token_urlsafe(24)
    display = f"{plaintext[: len(OPERATOR_PREFIX) + 4]}…{plaintext[-4:]}"
    return plaintext, hash_key(plaintext), display


def extract_token(authorization: str | None, x_admin_token: str | None) -> str | None:
    """Pull a bearer token from either the Authorization or X-Admin-Token header."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if x_admin_token:
        return x_admin_token.strip()
    return None


def _token_from_request(request: Request) -> str | None:
    return extract_token(
        request.headers.get("authorization"), request.headers.get("x-admin-token")
    )


async def resolve_actor(request: Request) -> tuple[str | None, Role | None]:
    """Map a request to (actor, role), applying the compatibility rules in order:

    1. auth disabled (no env admin token) **and** no operators created yet →
       open local-dev: ("local-dev", ADMIN).
    2. token matches a non-revoked operator → (label, its role).
    3. token equals the env admin token → ("admin", ADMIN) — the permanent
       rescue/bootstrap backdoor.
    4. otherwise → (None, None); the caller raises 401.
    """
    settings = request.app.state.settings
    store = getattr(request.app.state, "store", None)
    token = _token_from_request(request)

    n_ops = await store.count_active_operators() if store is not None else 0

    # 1. Frictionless local dev: nothing configured at all.
    if not settings.auth_enabled and n_ops == 0:
        return "local-dev", Role.ADMIN

    # 2. A named operator credential.
    if token and store is not None:
        op = await store.get_active_operator_by_hash(hash_key(token))
        if op:
            await store.touch_operator(op["id"])
            try:
                return op["label"], Role(op["role"])
            except ValueError:  # unknown role string in DB → treat as lowest
                return op["label"], Role.VIEWER

    # 3. The env admin token (always admin; works even with operators present).
    if token and settings.admin_token and secrets.compare_digest(token, settings.admin_token):
        return "admin", Role.ADMIN

    # 4. No valid credential.
    return None, None


def require_role(min_role: Role):
    """Build a dependency that requires at least ``min_role`` and stashes the
    resolved (actor, role) on ``request.state`` for the audit middleware."""

    async def dependency(request: Request) -> None:
        actor, role = await resolve_actor(request)
        if role is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Stash identity before the rank check so the audit trail attributes even
        # a rejected (403) attempt to the real caller.
        request.state.actor = actor
        request.state.role = role.value
        if _RANK[role] < _RANK[min_role]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"insufficient role: '{role.value}' (need '{min_role.value}')",
            )

    return dependency


# Named guards. ``require_admin`` keeps its historical name (every existing
# Depends(require_admin) now means role=admin) for backward compatibility.
require_admin = require_role(Role.ADMIN)
require_operator = require_role(Role.OPERATOR)
require_viewer = require_role(Role.VIEWER)

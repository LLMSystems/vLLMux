"""Notification sinks + test push (admin-gated).

DB-backed alert sinks layered on top of the env-configured ones. Every mutation
refreshes the live Notifier so changes take effect without a restart. URLs often
embed a secret (Slack/Discord webhook tokens), so list responses only return a
masked preview — never the full URL.
"""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.auth import require_admin
from app.llmops.notifier import Sink, refresh_sinks

router = APIRouter(prefix="/alerts", tags=["alerts"])

SinkType = Literal["slack", "discord", "webhook"]
Severity = Literal["info", "warning", "error", "critical"]


def _mask_url(url: str) -> str:
    """Hide the secret-bearing path; show scheme://host + last 4 chars."""
    try:
        p = urlparse(url)
        host = p.netloc or "?"
        tail = url[-4:] if len(url) > 4 else "****"
        return f"{p.scheme}://{host}/…{tail}"
    except Exception:
        return "…"


def _store(request: Request):
    return request.app.state.store


def _notifier(request: Request):
    return getattr(request.app.state, "notifier", None)


async def _reload(request: Request) -> None:
    n = _notifier(request)
    if n is not None:
        await refresh_sinks(n, request.app.state.settings, _store(request))


class CreateSinkRequest(BaseModel):
    type: SinkType
    url: str = Field(min_length=1)
    min_severity: Severity = "info"


class TestRequest(BaseModel):
    id: int | None = None  # a specific DB sink; omitted -> all active sinks


@router.get("/sinks", dependencies=[Depends(require_admin)])
async def list_sinks(request: Request):
    """Active sinks = env (built-in, not editable) + DB. URLs masked."""
    settings = request.app.state.settings
    out: list[dict] = []
    from app.llmops.notifier import env_sinks
    for s in env_sinks(settings):
        out.append({"id": None, "type": s.type, "url_preview": _mask_url(s.url),
                    "min_severity": s.min_severity, "source": "env"})
    for row in await _store(request).list_alert_sinks():
        out.append({"id": row["id"], "type": row["type"],
                    "url_preview": _mask_url(row["url"]),
                    "min_severity": row["min_severity"], "source": "db"})
    return out


@router.post("/sinks", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
async def create_sink(body: CreateSinkRequest, request: Request):
    sink_id = await _store(request).create_alert_sink(
        body.type, body.url.strip(), body.min_severity
    )
    await _reload(request)
    return {"id": sink_id, "type": body.type, "url_preview": _mask_url(body.url.strip()),
            "min_severity": body.min_severity, "source": "db"}


@router.delete("/sinks/{sink_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def delete_sink(sink_id: int, request: Request):
    if not await _store(request).delete_alert_sink(sink_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown sink: {sink_id}")
    await _reload(request)


@router.post("/test", dependencies=[Depends(require_admin)])
async def test_sinks(request: Request, body: TestRequest | None = None):
    """Fire a synthetic alert to one DB sink (by id) or all active sinks."""
    notifier = _notifier(request)
    if notifier is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "notifier unavailable")
    targets = None
    if body and body.id is not None:
        row = await _store(request).get_alert_sink(body.id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown sink: {body.id}")
        targets = [Sink(row["type"], row["url"], row["min_severity"])]
    if targets is None and not notifier.sinks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no sinks configured")
    return {"results": await notifier.send_test(targets)}

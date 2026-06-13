"""Readiness probing.

A model is "ready" when its HTTP health endpoint answers 200. vLLM exposes
`/health`; the embedding/reranker server gains a `/health` in this refactor, so
a single HTTP probe covers both kinds.

The probe takes the shared app.state.http_client (an httpx.AsyncClient) so it
reuses connection pooling and timeouts; any error means "not ready".
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def is_ready(http_client, probe_url: str) -> bool:
    try:
        resp = await http_client.get(probe_url)
        return resp.status_code == 200
    except Exception:
        return False

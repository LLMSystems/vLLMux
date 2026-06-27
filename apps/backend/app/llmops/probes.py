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


async def is_sleeping(http_client, base_url: str) -> bool:
    """True iff a sleep-mode vLLM reports it is asleep (level-1/2).

    Queries the dev endpoint `GET /is_sleeping` (only present when launched with
    VLLM_SERVER_DEV_MODE=1 + --enable-sleep-mode). Any error / missing endpoint is
    treated as "not sleeping" so a non-sleep-capable server is never misjudged.
    """
    try:
        resp = await http_client.get(base_url.rstrip("/") + "/is_sleeping")
        if resp.status_code != 200:
            return False
        return bool(resp.json().get("is_sleeping", False))
    except Exception:
        return False

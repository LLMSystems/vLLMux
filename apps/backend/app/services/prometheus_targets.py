"""Prometheus file-based service discovery for the backend-owned vLLM fleet.

vLLM instances are spawned on demand on dynamic localhost ports (and come and go
as models are added/removed/auto-restarted), so a static Prometheus scrape config
would constantly drift. Instead the backend — which already owns the registry, the
single source of truth for which instance is on which port — writes a Prometheus
`file_sd` targets file listing every *ready* vLLM instance. Prometheus watches the
file and picks up changes within its refresh interval, no restart needed.

Only LLM (vLLM) instances are emitted: vLLM exposes a Prometheus-format `/metrics`
on its OpenAI port, whereas the embedding/reranker server does not.

The file lives in the shared data volume and is read by the Prometheus container
(which joins the backend's network namespace, so the `localhost:<port>` targets
resolve to the same vLLM processes the backend spawned).
"""
from __future__ import annotations

import json
import os
from typing import Iterable

from app.llmops.instance import ModelInstance
from app.llmops.state import ModelKind, ModelState


def build_targets(instances: Iterable[ModelInstance]) -> list[dict]:
    """Build the Prometheus file_sd target list from registry instances.

    One entry per ready vLLM instance. `targets` is the scrape address
    (`host:port`); Prometheus appends the configured metrics_path (`/metrics`).
    Labels carry the group/instance identity and model tag so dashboards can
    join on something meaningful instead of the volatile `host:port`.

    Sorted by address so the serialized output is stable — the writer can then
    skip an identical rewrite and avoid churning the file (which would otherwise
    nudge Prometheus to re-read it every reconcile pass).
    """
    targets: list[dict] = []
    for inst in instances:
        if inst.kind != ModelKind.LLM or inst.state != ModelState.READY:
            continue
        group, _, instance_id = inst.key.partition("::")
        targets.append(
            {
                "targets": [f"{inst.host}:{inst.port}"],
                "labels": {
                    "group": group,
                    "instance_id": instance_id,
                    "model_tag": inst.model_tag or "",
                },
            }
        )
    targets.sort(key=lambda t: t["targets"][0])
    return targets


def render(targets: list[dict]) -> str:
    """Serialize the target list to the JSON Prometheus file_sd expects."""
    return json.dumps(targets, indent=2, sort_keys=True)


def write_targets_file(path: str, targets: list[dict]) -> bool:
    """Atomically write the SD file if its content changed. Returns True if it
    was (re)written, False if the on-disk content already matched.

    Write-if-changed keeps Prometheus from re-reading an identical file on every
    reconcile tick. The write is atomic (temp + os.replace) so Prometheus never
    observes a half-written, unparseable file.
    """
    payload = render(targets)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == payload:
                return False
    except (OSError, ValueError):
        pass  # missing/unreadable -> (re)write below

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, path)  # atomic on POSIX
    return True

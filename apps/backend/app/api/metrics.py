"""Prometheus exposition of autoscaling metrics (Phase 4).

Hand-rolled text format (no prometheus_client dependency) over the per-group load
the backend already aggregates plus the autoscaler's action counters. Scraped by
the bundled Prometheus (job: llmops-backend) and rendered in the autoscaling
Grafana dashboard. `render_metrics` is pure for unit testing.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["metrics"])

_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# (metric name, load_stats field, HELP) — all gauges.
_GAUGES = [
    ("llmops_group_ready_replicas", "ready_replicas", "Ready (serving) replicas in the group"),
    ("llmops_group_asleep_replicas", "asleep_replicas", "Level-1 asleep replicas (VRAM freed)"),
    ("llmops_group_stopped_replicas", "stopped_replicas", "Stopped replicas"),
    ("llmops_group_waiting_total", "waiting_total", "Queued (waiting) requests across ready replicas"),
    ("llmops_group_running_total", "running_total", "In-flight (running) requests across ready replicas"),
    ("llmops_group_waiting_per_replica", "waiting_per_replica", "Queue depth per ready replica (scale-up signal)"),
    ("llmops_group_kv_cache_avg", "kv_avg", "Mean KV-cache utilisation over ready replicas (0..1)"),
]


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def render_metrics(
    load_stats: dict,
    autoscale_by_group: dict,        # group -> autoscale dict | None
    action_counts: dict,             # (group, verb) -> int
    blocked_counts: dict,            # (group, reason) -> int
) -> str:
    out: list[str] = []

    for metric, field, help_ in _GAUGES:
        out.append(f"# HELP {metric} {help_}")
        out.append(f"# TYPE {metric} gauge")
        for group, st in sorted(load_stats.items()):
            out.append(f'{metric}{{group="{_esc(group)}"}} {_num(st.get(field))}')

    # Autoscale policy as gauges (thresholds/bounds for dashboard overlay lines).
    out.append("# HELP llmops_group_autoscale_enabled Autoscaling enabled (1) or off (0)")
    out.append("# TYPE llmops_group_autoscale_enabled gauge")
    for group, a in sorted(autoscale_by_group.items()):
        enabled = 1 if (a and a.get("enabled")) else 0
        out.append(f'llmops_group_autoscale_enabled{{group="{_esc(group)}"}} {enabled}')

    for metric, field in [
        ("llmops_group_min_ready", "min_ready"),
        ("llmops_group_max_ready", "max_ready"),
        ("llmops_group_scale_up_waiting", "scale_up_waiting"),
    ]:
        out.append(f"# TYPE {metric} gauge")
        for group, a in sorted(autoscale_by_group.items()):
            if a is None or a.get(field) is None:
                continue
            out.append(f'{metric}{{group="{_esc(group)}"}} {_num(a.get(field))}')

    out.append("# HELP llmops_autoscale_actions_total Autoscaler actions taken")
    out.append("# TYPE llmops_autoscale_actions_total counter")
    for (group, verb), n in sorted(action_counts.items()):
        out.append(f'llmops_autoscale_actions_total{{group="{_esc(group)}",action="{_esc(verb)}"}} {n}')

    out.append("# HELP llmops_autoscale_blocked_total Scale-up attempts blocked (e.g. by VRAM preflight)")
    out.append("# TYPE llmops_autoscale_blocked_total counter")
    for (group, reason), n in sorted(blocked_counts.items()):
        out.append(f'llmops_autoscale_blocked_total{{group="{_esc(group)}",reason="{_esc(reason)}"}} {n}')

    return "\n".join(out) + "\n"


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    app = request.app
    load_stats = getattr(app.state, "load_stats", {}) or {}
    manager = getattr(app.state, "manager", None)
    autoscale_by_group: dict = {}
    if manager is not None:
        for group, engine in manager.config.LLM_engines.items():
            a = getattr(engine, "autoscale", None)
            autoscale_by_group[group] = a.model_dump() if a is not None else None
    autoscaler = getattr(app.state, "autoscaler", None)
    action_counts = getattr(autoscaler, "action_counts", {}) if autoscaler else {}
    blocked_counts = getattr(autoscaler, "blocked_counts", {}) if autoscaler else {}

    body = render_metrics(load_stats, autoscale_by_group, action_counts, blocked_counts)
    return Response(content=body, media_type=_PROM_CONTENT_TYPE)

"""Prometheus exposition (Phase 4): render_metrics text format."""
import pytest

from app.api.metrics import render_metrics

pytestmark = pytest.mark.unit


def test_render_gauges_config_and_counters():
    load_stats = {"Qwen": {
        "ready_replicas": 2, "asleep_replicas": 1, "stopped_replicas": 3,
        "waiting_total": 4, "running_total": 5, "waiting_per_replica": 2.0, "kv_avg": 0.5,
    }}
    autoscale = {"Qwen": {"enabled": True, "min_ready": 1, "max_ready": 2, "scale_up_waiting": 4.0}}
    actions = {("Qwen", "start"): 3, ("Qwen", "sleep"): 1}
    blocked = {("Qwen", "vram"): 2}

    text = render_metrics(load_stats, autoscale, actions, blocked)

    assert 'llmops_group_ready_replicas{group="Qwen"} 2.0' in text
    assert 'llmops_group_waiting_per_replica{group="Qwen"} 2.0' in text
    assert 'llmops_group_autoscale_enabled{group="Qwen"} 1' in text
    assert 'llmops_group_max_ready{group="Qwen"} 2.0' in text
    assert 'llmops_group_scale_up_waiting{group="Qwen"} 4.0' in text
    assert 'llmops_autoscale_actions_total{group="Qwen",action="start"} 3' in text
    assert 'llmops_autoscale_blocked_total{group="Qwen",reason="vram"} 2' in text
    # exposition hygiene: a TYPE line per metric
    assert "# TYPE llmops_group_ready_replicas gauge" in text
    assert "# TYPE llmops_autoscale_actions_total counter" in text


def test_render_non_autoscaled_group_emits_enabled_zero_only():
    text = render_metrics({"Q": {}}, {"Q": None}, {}, {})
    assert 'llmops_group_autoscale_enabled{group="Q"} 0' in text
    # min_ready/max_ready have no sample for a group without a policy
    assert 'llmops_group_min_ready{group="Q"}' not in text


def test_render_endpoint_returns_prometheus_text(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "llmops_group_autoscale_enabled" in resp.text

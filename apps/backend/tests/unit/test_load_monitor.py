"""Per-group load aggregation (autoscaling Phase 1): registry state + router scrape."""
from types import SimpleNamespace

import pytest

from app.llmops.load_monitor import aggregate_load
from app.llmops.state import ModelKind, ModelState

pytestmark = pytest.mark.unit


def _inst(key, state, kind=ModelKind.LLM):
    return SimpleNamespace(key=key, state=state, kind=kind)


def test_aggregates_waiting_and_per_replica():
    instances = [
        _inst("Qwen::a", ModelState.READY),
        _inst("Qwen::b", ModelState.READY),
    ]
    metrics = {"Qwen": {
        "a": {"waiting": 4, "running": 2, "kv_cache_usage_perc": 0.5},
        "b": {"waiting": 6, "running": 1, "kv_cache_usage_perc": 0.3},
    }}
    out = aggregate_load(instances, metrics)["Qwen"]
    assert out["ready_replicas"] == 2
    assert out["waiting_total"] == 10
    assert out["running_total"] == 3
    assert out["waiting_per_replica"] == 5
    assert out["kv_avg"] == pytest.approx(0.4)


def test_counts_lifecycle_states():
    instances = [
        _inst("Qwen::a", ModelState.READY),
        _inst("Qwen::b", ModelState.SLEEPING),
        _inst("Qwen::c", ModelState.STOPPED),
        _inst("Qwen::d", ModelState.STOPPED),
    ]
    out = aggregate_load(instances, {"Qwen": {"a": {"waiting": 0}}})["Qwen"]
    assert (out["ready_replicas"], out["asleep_replicas"], out["stopped_replicas"]) == (1, 1, 2)


def test_only_ready_replicas_contribute_load():
    # The sleeping replica has stale metrics in the scrape; they must be ignored.
    instances = [
        _inst("Qwen::a", ModelState.READY),
        _inst("Qwen::b", ModelState.SLEEPING),
    ]
    metrics = {"Qwen": {"a": {"waiting": 3}, "b": {"waiting": 99}}}
    out = aggregate_load(instances, metrics)["Qwen"]
    assert out["waiting_total"] == 3
    assert out["waiting_per_replica"] == 3


def test_missing_or_null_metrics_treated_as_zero():
    instances = [_inst("Qwen::a", ModelState.READY)]
    # failed scrape -> router nulls the fields; no crash, counts as idle.
    metrics = {"Qwen": {"a": {"waiting": None, "running": None, "kv_cache_usage_perc": None}}}
    out = aggregate_load(instances, metrics)["Qwen"]
    assert out["waiting_total"] == 0
    assert out["kv_avg"] == 0


def test_no_ready_replicas_avoids_div_by_zero():
    instances = [_inst("Qwen::a", ModelState.STOPPED)]
    out = aggregate_load(instances, {})["Qwen"]
    assert out["ready_replicas"] == 0
    assert out["waiting_per_replica"] == 0


def test_non_llm_instances_excluded():
    instances = [
        _inst("emb", ModelState.READY, kind=ModelKind.EMBEDDING),
        _inst("Qwen::a", ModelState.READY),
    ]
    out = aggregate_load(instances, {"Qwen": {"a": {"waiting": 1}}})
    assert set(out) == {"Qwen"}

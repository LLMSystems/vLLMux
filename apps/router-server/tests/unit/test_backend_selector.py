import time

import pytest
from fastapi import HTTPException

from src.llm_router.backend_runtime_state import make_backend_key
from src.llm_router.backend_selector import select_instance_least_load
from src.llm_router.vllm_metrics_client import VLLMInstanceMetrics

pytestmark = pytest.mark.unit

MODEL = "Qwen3-0.6B"
INSTANCES = [{"id": "a", "port": 8002}, {"id": "b", "port": 8004}]


def metric(running=0.0, waiting=0.0, kv=0.0):
    return VLLMInstanceMetrics(
        base_url="x", running=running, waiting=waiting, kv_cache_usage_perc=kv
    )


async def test_single_instance_returned_without_metrics(make_app):
    cfg = {"instances": [{"id": "solo", "port": 8000}]}
    res = await select_instance_least_load(make_app(), MODEL, cfg)
    assert res["id"] == "solo"


async def test_no_instances_raises_500(make_app):
    with pytest.raises(HTTPException) as exc:
        await select_instance_least_load(make_app(), MODEL, {"instances": []})
    assert exc.value.status_code == 500


async def test_picks_lowest_load_score(make_app):
    metrics = {MODEL: {"a": metric(running=10), "b": metric(running=1)}}
    res = await select_instance_least_load(
        make_app(metrics_cache=metrics), MODEL, {"instances": INSTANCES}
    )
    assert res["id"] == "b"


async def test_inflight_penalty_can_flip_the_choice(make_app):
    # base scores: a=0, b=3. Two in-flight on a => +2*5 = 10, so a=10 > b=3.
    metrics = {MODEL: {"a": metric(running=0), "b": metric(running=1)}}
    app = make_app(
        metrics_cache=metrics, backend_inflight={make_backend_key(MODEL, "a"): 2}
    )
    res = await select_instance_least_load(app, MODEL, {"instances": INSTANCES})
    assert res["id"] == "b"


async def test_cooldown_instance_is_avoided(make_app):
    # a is least loaded but in cooldown (+1e9 penalty) => pick b despite higher load.
    metrics = {MODEL: {"a": metric(running=0), "b": metric(running=100)}}
    health = {make_backend_key(MODEL, "a"): {"cooldown_until": time.time() + 100}}
    app = make_app(metrics_cache=metrics, backend_health=health)
    res = await select_instance_least_load(app, MODEL, {"instances": INSTANCES})
    assert res["id"] == "b"


async def test_instance_without_cached_metric_is_skipped(make_app):
    metrics = {MODEL: {"b": metric(running=5)}}  # "a" has no metric
    res = await select_instance_least_load(
        make_app(metrics_cache=metrics), MODEL, {"instances": INSTANCES}
    )
    assert res["id"] == "b"


async def test_all_metrics_missing_raises_500(make_app):
    app = make_app(metrics_cache={MODEL: {}})
    with pytest.raises(HTTPException) as exc:
        await select_instance_least_load(app, MODEL, {"instances": INSTANCES})
    assert exc.value.status_code == 500

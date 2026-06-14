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


async def test_missing_metric_treated_as_idle(make_app):
    # "a" has no cached metric yet (cold start). It must stay selectable with an
    # idle (0) score rather than being skipped, so a freshly-scraped instance
    # wins over a loaded one instead of 500ing the request.
    metrics = {MODEL: {"b": metric(running=5)}}
    res = await select_instance_least_load(
        make_app(metrics_cache=metrics), MODEL, {"instances": INSTANCES}
    )
    assert res["id"] == "a"


async def test_all_metrics_missing_still_routes(make_app):
    # Before the first scrape lands, every instance is unknown. We must still
    # pick one (cold-start window) rather than 500.
    app = make_app(metrics_cache={MODEL: {}})
    res = await select_instance_least_load(app, MODEL, {"instances": INSTANCES})
    assert res["id"] in {"a", "b"}


async def test_missing_metric_still_respects_inflight(make_app):
    # Both unknown (score 0), but "a" already has an in-flight request, so the
    # inflight penalty should push the new one to "b".
    app = make_app(
        metrics_cache={MODEL: {}}, backend_inflight={make_backend_key(MODEL, "a"): 1}
    )
    res = await select_instance_least_load(app, MODEL, {"instances": INSTANCES})
    assert res["id"] == "b"

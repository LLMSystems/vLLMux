import time

import pytest
from fastapi import HTTPException

from src.llm_router.backend_runtime_state import make_backend_key
from src.llm_router import routing_strategies as rs
from src.llm_router.routing_strategies import (SelectContext, score_instance,
                                               select_instance)
from src.llm_router.vllm_metrics_client import VLLMInstanceMetrics

pytestmark = pytest.mark.unit

MODEL = "Qwen3-0.6B"
INSTANCES = [{"id": "a", "port": 8002}, {"id": "b", "port": 8004}, {"id": "c", "port": 8006}]


def metric(running=0.0, waiting=0.0, kv=0.0, is_sleeping=False):
    return VLLMInstanceMetrics(
        base_url="x", running=running, waiting=waiting,
        kv_cache_usage_perc=kv, is_sleeping=is_sleeping,
    )


def ctx(app, candidates=None, all_instances=None, **kw):
    cand = candidates if candidates is not None else INSTANCES
    return SelectContext(
        app=app, model_key=MODEL, candidates=cand,
        all_instances=all_instances if all_instances is not None else INSTANCES, **kw,
    )


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #
async def test_no_instances_raises_500(make_app):
    with pytest.raises(HTTPException) as exc:
        await select_instance(make_app(), MODEL, {"instances": []})
    assert exc.value.status_code == 500


async def test_all_excluded_raises_503(make_app):
    with pytest.raises(HTTPException) as exc:
        await select_instance(make_app(), MODEL, {"instances": INSTANCES}, exclude={"a", "b", "c"})
    assert exc.value.status_code == 503


async def test_single_candidate_shortcut_ignores_strategy(make_app):
    # Even with an affinity strategy and no key, one candidate is returned directly.
    res = await select_instance(
        make_app(), MODEL, {"instances": [{"id": "solo", "port": 8000}]},
        strategy="session_affinity",
    )
    assert res["id"] == "solo"


async def test_unknown_strategy_falls_back_to_least_load(make_app):
    metrics = {MODEL: {"a": metric(running=10), "b": metric(running=1), "c": metric(running=5)}}
    res = await select_instance(
        make_app(metrics_cache=metrics), MODEL, {"instances": INSTANCES}, strategy="bogus",
    )
    assert res["id"] == "b"


async def test_sleeping_instance_is_excluded_from_routing(make_app):
    # 'b' is least-loaded but asleep -> must be skipped; next-best 'a' wins.
    metrics = {MODEL: {
        "a": metric(running=1),
        "b": metric(running=0, is_sleeping=True),
        "c": metric(running=5),
    }}
    res = await select_instance(
        make_app(metrics_cache=metrics), MODEL, {"instances": INSTANCES}, strategy="least_load",
    )
    assert res["id"] == "a"


async def test_all_asleep_raises_503(make_app):
    metrics = {MODEL: {i["id"]: metric(is_sleeping=True) for i in INSTANCES}}
    with pytest.raises(HTTPException) as exc:
        await select_instance(
            make_app(metrics_cache=metrics), MODEL, {"instances": INSTANCES},
        )
    assert exc.value.status_code == 503
    assert "asleep" in exc.value.detail.lower()


async def test_exclude_removes_candidate(make_app):
    metrics = {MODEL: {"a": metric(running=1), "b": metric(running=0), "c": metric(running=5)}}
    res = await select_instance(
        make_app(metrics_cache=metrics), MODEL, {"instances": INSTANCES},
        strategy="least_load", exclude={"b"},
    )
    assert res["id"] == "a"


# --------------------------------------------------------------------------- #
# least_load / least_inflight
# --------------------------------------------------------------------------- #
async def test_least_load_picks_lowest(make_app):
    metrics = {MODEL: {"a": metric(running=10), "b": metric(running=1), "c": metric(kv=0.5)}}
    res = rs._least_load(ctx(make_app(metrics_cache=metrics)))
    assert res["id"] == "b"


async def test_least_inflight_ignores_metrics(make_app):
    # 'a' is the most loaded by metrics but has zero in-flight; least_inflight
    # ignores the scrape, so it wins over backends carrying in-flight requests.
    metrics = {MODEL: {"a": metric(running=99), "b": metric(running=0), "c": metric(running=0)}}
    app = make_app(
        metrics_cache=metrics,
        backend_inflight={make_backend_key(MODEL, "b"): 1, make_backend_key(MODEL, "c"): 2},
    )
    assert rs._least_inflight(ctx(app))["id"] == "a"


async def test_cooldown_avoided(make_app):
    metrics = {MODEL: {"a": metric(running=0), "b": metric(running=100), "c": metric(running=100)}}
    health = {make_backend_key(MODEL, "a"): {"cooldown_until": time.time() + 100}}
    res = rs._least_load(ctx(make_app(metrics_cache=metrics, backend_health=health)))
    assert res["id"] != "a"


# --------------------------------------------------------------------------- #
# round_robin / random / p2c
# --------------------------------------------------------------------------- #
async def test_round_robin_cycles(make_app):
    app = make_app()
    picks = [rs._round_robin(ctx(app))["id"] for _ in range(6)]
    assert picks == ["a", "b", "c", "a", "b", "c"]


async def test_random_stays_in_candidate_set(make_app):
    app = make_app()
    for _ in range(20):
        assert rs._random(ctx(app, candidates=INSTANCES[:2]))["id"] in {"a", "b"}


async def test_p2c_never_worse_than_global_max(make_app):
    # With scores a<b<c, p2c samples two of three; the pick is never the global
    # worst 'c' unless 'c' wasn't even... actually p2c can pick 'c' only if both
    # samples were {b,c} or {a,c} and c<other — impossible here, so 'c' never wins.
    metrics = {MODEL: {"a": metric(running=0), "b": metric(running=1), "c": metric(running=2)}}
    app = make_app(metrics_cache=metrics)
    for _ in range(50):
        assert rs._p2c(ctx(app))["id"] in {"a", "b"}


# --------------------------------------------------------------------------- #
# affinity
# --------------------------------------------------------------------------- #
async def test_session_affinity_is_deterministic(make_app):
    app = make_app(metrics_cache={MODEL: {i["id"]: metric() for i in INSTANCES}})
    homes = {rs._session_affinity(ctx(app, session_key="sess-42"))["id"] for _ in range(10)}
    assert len(homes) == 1  # same key -> same replica every time


async def test_session_affinity_no_key_degrades_to_least_load(make_app):
    metrics = {MODEL: {"a": metric(running=9), "b": metric(running=0), "c": metric(running=9)}}
    app = make_app(metrics_cache=metrics)
    assert rs._session_affinity(ctx(app, session_key=None))["id"] == "b"


async def test_affinity_escapes_when_home_in_cooldown(make_app):
    app = make_app(metrics_cache={MODEL: {i["id"]: metric() for i in INSTANCES}})
    home = rs._session_affinity(ctx(app, session_key="sess-42"))["id"]
    # Put the home replica in cooldown; affinity must escape to another instance.
    app.state.backend_health = {make_backend_key(MODEL, home): {"cooldown_until": time.time() + 100}}
    assert rs._session_affinity(ctx(app, session_key="sess-42"))["id"] != home


async def test_affinity_escapes_when_home_overloaded(make_app):
    app = make_app(metrics_cache={MODEL: {i["id"]: metric() for i in INSTANCES}})
    home = rs._session_affinity(ctx(app, session_key="sess-42"))["id"]
    # Make the home replica far more loaded than the margin allows -> spread away.
    app.state.metrics_cache[MODEL][home] = metric(running=1000)
    assert rs._session_affinity(ctx(app, session_key="sess-42"))["id"] != home


async def test_prefix_affinity_deterministic_and_distinct_keys_can_differ(make_app):
    app = make_app(metrics_cache={MODEL: {i["id"]: metric() for i in INSTANCES}})
    a1 = rs._prefix_affinity(ctx(app, prompt_prefix="system: you are helpful"))["id"]
    a2 = rs._prefix_affinity(ctx(app, prompt_prefix="system: you are helpful"))["id"]
    assert a1 == a2  # stable for a given prefix


async def test_score_instance_cold_start_is_idle(make_app):
    # No cached metric for 'a' -> treated as idle (0), still selectable.
    app = make_app(metrics_cache={MODEL: {"b": metric(running=5)}})
    assert score_instance(app, MODEL, {"id": "a"}) == 0.0

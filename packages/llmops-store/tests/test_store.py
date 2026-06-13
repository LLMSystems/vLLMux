import os
import sys

import pytest

# Make `llmops_store` importable without a conftest (a conftest here would join
# the top-level `tests` namespace and shadow sibling apps' test conftests).
PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_ROOT not in sys.path:
    sys.path.insert(0, PKG_ROOT)

from llmops_store import LLMOpsStore, _percentile  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
async def store(tmp_path):
    s = await LLMOpsStore(str(tmp_path / "test.db")).init()
    yield s
    await s.close()


def test_percentile_nearest_rank():
    vals = [10, 20, 30, 40, 50]
    assert _percentile(vals, 50) == 30
    assert _percentile(vals, 95) == 50
    assert _percentile([], 50) is None
    assert _percentile([7], 95) == 7


async def test_init_is_idempotent(tmp_path):
    path = str(tmp_path / "x.db")
    s1 = await LLMOpsStore(path).init()
    await s1.close()
    # Second init on the same file must not error (CREATE TABLE IF NOT EXISTS).
    s2 = await LLMOpsStore(path).init()
    await s2.close()


async def test_record_and_query_model_events(store):
    await store.record_model_event("Qwen::a", "llm", "stopped", "starting")
    await store.record_model_event("Qwen::a", "llm", "starting", "ready")
    await store.record_model_event("Other::b", "llm", "stopped", "starting")

    all_events = await store.recent_events()
    assert len(all_events) == 3

    only_a = await store.recent_events(key="Qwen::a")
    assert [e["to_state"] for e in only_a] == ["ready", "starting"]  # newest first
    assert only_a[0]["from_state"] == "starting"


async def test_record_and_query_requests(store):
    await store.record_request("Qwen", "/v1/chat/completions", status_code=200, latency_ms=12.0)
    await store.record_request("Qwen", "/v1/completions", status_code=500, latency_ms=5.0, error="boom")

    rows = await store.recent_requests(model_key="Qwen")
    assert len(rows) == 2
    assert rows[0]["status_code"] == 500
    assert rows[0]["error"] == "boom"


async def test_usage_summary_aggregates(store):
    for lat in (10.0, 20.0, 30.0, 40.0):
        await store.record_request("Qwen", "/v1/chat/completions", status_code=200,
                                   latency_ms=lat, total_tokens=100)
    await store.record_request("Qwen", "/v1/chat/completions", status_code=503,
                               latency_ms=99.0, error="down")

    summary = {row["model_key"]: row for row in await store.usage_summary()}
    q = summary["Qwen"]
    assert q["count"] == 5
    assert q["error_count"] == 1            # the 503
    assert q["total_tokens"] == 400         # 4 x 100 (error row had none)
    assert q["max_latency_ms"] == 99.0
    assert q["p50_latency_ms"] is not None
    assert q["p95_latency_ms"] is not None


async def test_usage_summary_since_filter(store):
    await store.record_request("Qwen", "/v1/chat/completions", status_code=200,
                               latency_ms=10.0, ts=1000.0)
    await store.record_request("Qwen", "/v1/chat/completions", status_code=200,
                               latency_ms=10.0, ts=2000.0)
    recent = {r["model_key"]: r for r in await store.usage_summary(since=1500.0)}
    assert recent["Qwen"]["count"] == 1

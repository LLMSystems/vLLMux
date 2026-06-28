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


async def test_api_key_lifecycle(store):
    kid = await store.create_api_key("ci", "hash-abc", "sk-llmops-AbC…")
    # validate by hash
    row = await store.get_active_api_key_by_hash("hash-abc")
    assert row is not None and row["name"] == "ci" and row["id"] == kid
    assert await store.get_active_api_key_by_hash("nope") is None
    # list never exposes the hash
    listed = await store.list_api_keys()
    assert len(listed) == 1 and "key_hash" not in listed[0]
    # revoke -> no longer validates
    assert await store.revoke_api_key(kid) is True
    assert await store.get_active_api_key_by_hash("hash-abc") is None
    assert await store.revoke_api_key(kid) is False  # already revoked


async def test_api_key_token_quota_persisted(store):
    kid = await store.create_api_key(
        "team-rag", "hash-q", "sk-llmops-Q…", token_quota=1000, quota_period="daily"
    )
    row = await store.get_active_api_key_by_hash("hash-q")
    assert row["token_quota"] == 1000 and row["quota_period"] == "daily"
    listed = await store.list_api_keys()
    assert listed[0]["id"] == kid and listed[0]["token_quota"] == 1000


async def test_tokens_used_by_key_sums_and_windows(store):
    await store.create_api_key("k", "h", "sk-llmops-K…", token_quota=500)
    await store.record_request(
        model_key="m", path="/v1/chat/completions", total_tokens=100,
        api_key_name="k", ts=1000.0,
    )
    await store.record_request(
        model_key="m", path="/v1/chat/completions", total_tokens=250,
        api_key_name="k", ts=2000.0,
    )
    # another key's usage must not leak in
    await store.record_request(
        model_key="m", path="/v1/chat/completions", total_tokens=999,
        api_key_name="other", ts=2000.0,
    )
    assert await store.tokens_used_by_key("k") == 350
    assert await store.tokens_used_by_key("k", since=1500.0) == 250  # only the 2nd row


async def test_record_request_with_api_key_attribution(store):
    await store.record_request(
        model_key="Qwen::a", path="/v1/chat/completions", status_code=200,
        total_tokens=42, api_key_name="ci",
    )
    rows = await store.recent_requests()
    assert rows[0]["api_key_name"] == "ci"


async def test_migration_adds_api_key_name_to_legacy_db(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy.db")
    # Simulate an old DB: a request_logs table predating api_key_name. Seed it
    # synchronously (closed before the async store opens it) to avoid two live
    # connections racing on the same file.
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE request_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, "
        "model_key TEXT, instance_id TEXT, path TEXT, status_code INTEGER, "
        "latency_ms REAL, prompt_tokens INTEGER, completion_tokens INTEGER, "
        "total_tokens INTEGER, error TEXT)"
    )
    con.commit()
    con.close()

    # init() must migrate the new column in without error.
    s = await LLMOpsStore(path).init()
    await s.record_request(model_key="m", path="/v1/x", api_key_name="k")
    rows = await s.recent_requests()
    assert rows[0]["api_key_name"] == "k"
    await s.close()


async def test_perf_run_lifecycle(store):
    rid = await store.create_perf_run(model="Qwen", target_url="http://r/v1/chat/completions",
                                      params='{"parallel":[1,4]}', name="ci")
    run = await store.get_perf_run(rid)
    assert run["status"] == "running" and run["model"] == "Qwen"
    await store.finish_perf_run(rid, "completed", result='[{"rps":12}]', output_dir="/out")
    run = await store.get_perf_run(rid)
    assert run["status"] == "completed" and run["result"] == '[{"rps":12}]'
    assert any(r["id"] == rid for r in await store.list_perf_runs())
    assert await store.delete_perf_run(rid) is True
    assert await store.get_perf_run(rid) is None


async def test_mark_stale_perf_runs(store):
    rid = await store.create_perf_run(model="M", target_url="u", params="{}")
    await store.mark_stale_perf_runs()
    run = await store.get_perf_run(rid)
    assert run["status"] == "failed" and "restart" in run["error"]


async def test_eval_run_lifecycle(store):
    rid = await store.create_eval_run(model="Qwen", target_url="http://r/v1",
                                      datasets='["gsm8k","mmlu"]', params='{"limit":5}', name="ci")
    run = await store.get_eval_run(rid)
    assert run["status"] == "running" and run["model"] == "Qwen"
    assert run["datasets"] == '["gsm8k","mmlu"]'
    await store.finish_eval_run(rid, "completed", result='{"datasets":[{"dataset":"gsm8k","score":0.4}]}',
                                output_dir="/out")
    run = await store.get_eval_run(rid)
    assert run["status"] == "completed" and "gsm8k" in run["result"]
    assert any(r["id"] == rid for r in await store.list_eval_runs())
    assert await store.delete_eval_run(rid) is True
    assert await store.get_eval_run(rid) is None


async def test_mark_stale_eval_runs(store):
    rid = await store.create_eval_run(model="M", target_url="u", datasets="[]", params="{}")
    await store.mark_stale_eval_runs()
    run = await store.get_eval_run(rid)
    assert run["status"] == "failed" and "restart" in run["error"]


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


# -- Operators -------------------------------------------------------------

async def test_operator_crud_and_lookup(store):
    oid = await store.create_operator("alice", "hash-a", "sk-op-aaaa…zzzz", "admin")
    assert await store.count_active_operators() == 1

    op = await store.get_active_operator_by_hash("hash-a")
    assert op["label"] == "alice" and op["role"] == "admin" and op["id"] == oid

    listed = await store.list_operators()
    assert len(listed) == 1 and "token_hash" not in listed[0]

    assert await store.revoke_operator(oid) is True
    assert await store.revoke_operator(oid) is False          # already revoked
    assert await store.get_active_operator_by_hash("hash-a") is None
    assert await store.count_active_operators() == 0


async def test_operator_touch_updates_last_used(store):
    oid = await store.create_operator("bob", "hash-b", "sk-op-bbbb…zzzz", "operator")
    await store.touch_operator(oid, ts=1234.0)
    assert (await store.list_operators())[0]["last_used_at"] == 1234.0


# -- Audit log -------------------------------------------------------------

async def test_audit_record_and_filter(store):
    await store.record_audit("alice", "POST", "/api/models/Q/start", 202,
                             role="operator", target="Q", ts=1000.0)
    await store.record_audit("bob", "PUT", "/api/models/Q/autoscale", 200,
                             role="admin", target="Q", ts=2000.0)
    await store.record_audit("alice", "DELETE", "/api/keys/3", 204,
                             role="admin", target="3", ts=3000.0)

    assert len(await store.list_audit()) == 3
    assert [r["actor"] for r in await store.list_audit()] == ["alice", "bob", "alice"]  # newest first
    assert len(await store.list_audit(actor="alice")) == 2
    assert len(await store.list_audit(action="autoscale")) == 1
    assert len(await store.list_audit(target="Q")) == 2
    assert len(await store.list_audit(since=2500.0)) == 1
    assert len(await store.list_audit(until=1500.0)) == 1


async def test_audit_prune_keeps_newest(store):
    for i in range(10):
        await store.record_audit("a", "POST", f"/api/x/{i}", 200, ts=float(i))
    deleted = await store.prune_audit(max_rows=4)
    assert deleted == 6
    remaining = await store.list_audit(limit=100)
    assert len(remaining) == 4
    assert {r["path"] for r in remaining} == {"/api/x/9", "/api/x/8", "/api/x/7", "/api/x/6"}

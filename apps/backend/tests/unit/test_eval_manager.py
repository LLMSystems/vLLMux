"""EvalManager config-building (no subprocess)."""
from types import SimpleNamespace

import pytest

from app.core.settings import BackendSettings
from app.eval.manager import EvalError, EvalManager

pytestmark = pytest.mark.unit


def _manager(tmp_path):
    engine = SimpleNamespace(
        settings=SimpleNamespace(model_tag="Qwen/Qwen2.5-0.5B-Instruct"),
        instances=[SimpleNamespace(id="a", port=8006)],
    )
    fake_mgr = SimpleNamespace(config=SimpleNamespace(LLM_engines={"Qwen2.5-0.5B": engine}))
    settings = BackendSettings(admin_token="adm")
    return EvalManager(None, fake_mgr, settings, str(tmp_path), "http://127.0.0.1:8887")


def test_build_cfg_router_target(tmp_path):
    em = _manager(tmp_path)
    cfg = em._build_cfg(
        {"model": "Qwen2.5-0.5B", "target": "router", "datasets": ["gsm8k", "mmlu"],
         "limit": 5, "temperature": 0.0, "max_tokens": 1024},
        str(tmp_path / "1"),
    )
    assert cfg["api_url"] == "http://127.0.0.1:8887/v1"
    assert cfg["model"] == "Qwen2.5-0.5B"  # router routes by group key
    assert cfg["eval_type"] == "openai_api"
    assert cfg["datasets"] == ["gsm8k", "mmlu"]
    assert cfg["limit"] == 5
    assert cfg["generation_config"] == {"temperature": 0.0, "max_tokens": 1024}
    assert cfg["api_key"] == "adm"
    assert cfg["work_dir"] == str(tmp_path / "1") and cfg["no_timestamp"] is True
    assert cfg["ignore_errors"] is True


def test_build_cfg_instance_target_uses_port_and_tag(tmp_path):
    em = _manager(tmp_path)
    cfg = em._build_cfg(
        {"model": "Qwen2.5-0.5B", "target": "instance", "instance_key": "Qwen2.5-0.5B::a",
         "datasets": ["gsm8k"]},
        str(tmp_path / "1"),
    )
    assert cfg["api_url"] == "http://127.0.0.1:8006/v1"
    assert cfg["model"] == "Qwen/Qwen2.5-0.5B-Instruct"  # direct to vLLM by served tag


def test_build_cfg_no_limit_means_full(tmp_path):
    em = _manager(tmp_path)
    cfg = em._build_cfg(
        {"model": "Qwen2.5-0.5B", "target": "router", "datasets": ["gsm8k"], "limit": None},
        str(tmp_path / "1"),
    )
    assert "limit" not in cfg  # full dataset


def test_build_cfg_no_judge_uses_rule_strategy(tmp_path):
    em = _manager(tmp_path)
    cfg = em._build_cfg(
        {"model": "Qwen2.5-0.5B", "target": "router", "datasets": ["gsm8k"]},
        str(tmp_path / "1"),
    )
    assert cfg["judge_strategy"] == "rule"
    assert "judge_model_args" not in cfg


def test_build_cfg_internal_judge_via_router(tmp_path):
    em = _manager(tmp_path)
    cfg = em._build_cfg(
        {"model": "Qwen2.5-0.5B", "target": "router", "datasets": ["simple_qa"],
         "judge_enabled": True, "judge_strategy": "auto",
         "judge_target": "internal", "judge_model": "Qwen2.5-0.5B"},
        str(tmp_path / "1"),
    )
    assert cfg["judge_strategy"] == "auto"
    assert cfg["judge_model_args"] == {
        "model_id": "Qwen2.5-0.5B", "api_url": "http://127.0.0.1:8887/v1", "api_key": "adm",
        "generation_config": {"max_tokens": 2048, "temperature": 0.0},
    }


def test_build_cfg_external_judge(tmp_path):
    em = _manager(tmp_path)
    cfg = em._build_cfg(
        {"model": "Qwen2.5-0.5B", "target": "router", "datasets": ["simple_qa"],
         "judge_enabled": True, "judge_target": "external", "judge_model": "gpt-4o-mini",
         "judge_api_url": "https://api.openai.com/v1", "judge_api_key": "sk-x"},
        str(tmp_path / "1"),
    )
    assert cfg["judge_model_args"] == {
        "model_id": "gpt-4o-mini", "api_url": "https://api.openai.com/v1", "api_key": "sk-x",
        "generation_config": {"max_tokens": 2048, "temperature": 0.0},
    }


def test_extract_error_pulls_real_cause(tmp_path):
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    (run_dir / "run.log").write_text(
        "INFO some noise\n"
        "\x1b[33mWARNING\x1b[0m: Attempt 1 / 5 failed: Error code: 400 - "
        "{'error': {'message': \"This model's maximum context length is 5000 tokens\"}}\n"
        "more noise\n",
        encoding="utf-8",
    )
    msg = EvalManager._extract_error(str(run_dir))
    assert msg and "Error code: 400" in msg and "\x1b" not in msg


def test_extract_error_none_when_no_log(tmp_path):
    assert EvalManager._extract_error(str(tmp_path / "missing")) is None


def test_resolve_unknown_group_raises(tmp_path):
    em = _manager(tmp_path)
    with pytest.raises(EvalError):
        em._resolve("ghost", "router", None)


def test_resolve_unknown_instance_raises(tmp_path):
    em = _manager(tmp_path)
    with pytest.raises(EvalError):
        em._resolve("Qwen2.5-0.5B", "instance", "Qwen2.5-0.5B::nope")


def _lora_manager(tmp_path):
    engine = SimpleNamespace(
        settings=SimpleNamespace(
            model_tag="meta-llama/Llama-3.2-3B-Instruct",
            lora_modules=[SimpleNamespace(name="sql-lora", path="repo/sql")],
        ),
        instances=[SimpleNamespace(id="a", port=8000)],
    )
    fake_mgr = SimpleNamespace(config=SimpleNamespace(LLM_engines={"Llama": engine}))
    return EvalManager(None, fake_mgr, BackendSettings(admin_token="adm"), str(tmp_path), "http://127.0.0.1:8887")


def test_resolve_lora_via_router_keeps_served_name(tmp_path):
    em = _lora_manager(tmp_path)
    model_field, url = em._resolve("sql-lora", "router", None)
    assert model_field == "sql-lora"  # router routes LoRAs by served name
    assert url == "http://127.0.0.1:8887/v1"


def test_resolve_lora_on_instance_uses_served_name_not_tag(tmp_path):
    em = _lora_manager(tmp_path)
    model_field, url = em._resolve("sql-lora", "instance", "Llama::a")
    assert model_field == "sql-lora"  # vLLM serves the adapter under its name
    assert url == "http://127.0.0.1:8000/v1"


# ---- parallel queue (budget-based admission) -------------------------------

class _FakeStore:
    """Minimal store double for the queue tests (no SQLite, no subprocess)."""

    def __init__(self):
        self.runs: dict[int, dict] = {}
        self._id = 0

    async def create_eval_run(self, *, model, target_url, datasets, params, name, status):
        self._id += 1
        self.runs[self._id] = {"id": self._id, "model": model, "status": status}
        return self._id

    async def start_eval_run(self, run_id, ts=None):
        self.runs[run_id]["status"] = "running"

    async def finish_eval_run(self, run_id, status, **kw):
        self.runs[run_id]["status"] = status

    async def get_eval_run(self, run_id):
        return self.runs[run_id]


def _queue_manager(tmp_path, budget=32):
    engine = SimpleNamespace(
        settings=SimpleNamespace(model_tag="Qwen/Qwen2.5-0.5B-Instruct"),
        instances=[SimpleNamespace(id="a", port=8006)],
    )
    fake_mgr = SimpleNamespace(config=SimpleNamespace(LLM_engines={"Qwen2.5-0.5B": engine}))
    settings = BackendSettings(admin_token="adm", eval_concurrency_budget=budget)
    em = EvalManager(_FakeStore(), fake_mgr, settings, str(tmp_path), "http://127.0.0.1:8887")
    # Stub the real subprocess launch: just occupy a slot.
    launched: list[int] = []

    async def fake_launch(run_id):
        em._pending.pop(run_id, None)
        em._procs[run_id] = object()
        await em.store.start_eval_run(run_id)
        launched.append(run_id)

    em._launch = fake_launch  # type: ignore[method-assign]
    em._launched = launched   # type: ignore[attr-defined]
    return em


def _req(batch):
    return {"model": "Qwen2.5-0.5B", "target": "router", "datasets": ["gsm8k"],
            "eval_batch_size": batch}


async def test_queue_admits_within_budget_and_queues_the_rest(tmp_path):
    em = _queue_manager(tmp_path, budget=32)
    r1 = await em.start(_req(16))
    r2 = await em.start(_req(16))
    r3 = await em.start(_req(8))
    assert r1["status"] == "running" and r2["status"] == "running"  # 16+16 == 32
    assert r3["status"] == "queued"  # would overflow the budget
    assert em.used_budget == 32 and em._queued == [r3["id"]]


async def test_finishing_a_run_pumps_the_queue(tmp_path):
    em = _queue_manager(tmp_path, budget=32)
    await em.start(_req(16))
    await em.start(_req(16))
    r3 = await em.start(_req(8))
    assert r3["status"] == "queued"
    # Simulate run #1 finishing (the _watch tail does this, then pumps).
    em._procs.pop(1)
    em._batch.pop(1, None)
    await em._pump()
    assert em.store.runs[r3["id"]]["status"] == "running"
    assert r3["id"] in em._procs


async def test_lone_run_larger_than_budget_still_starts(tmp_path):
    em = _queue_manager(tmp_path, budget=8)
    r = await em.start(_req(64))  # nothing running -> admitted despite overflow
    assert r["status"] == "running"


async def test_load_test_blocks_the_queue(tmp_path):
    em = _queue_manager(tmp_path, budget=32)
    em.perf_manager = SimpleNamespace(busy=True)
    r = await em.start(_req(8))
    assert r["status"] == "queued" and not em._procs
    # Load test ends -> pump admits it.
    em.perf_manager.busy = False
    await em._pump()
    assert em.store.runs[r["id"]]["status"] == "running"


async def test_cancel_queued_run_removes_it(tmp_path):
    em = _queue_manager(tmp_path, budget=16)
    await em.start(_req(16))
    r2 = await em.start(_req(8))
    assert r2["status"] == "queued"
    assert await em.cancel(r2["id"]) is True
    assert r2["id"] not in em._queued
    assert em.store.runs[r2["id"]]["status"] == "cancelled"

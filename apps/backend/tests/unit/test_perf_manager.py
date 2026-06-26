"""PerfManager config-building + result-parsing (no subprocess)."""
import json
from types import SimpleNamespace

import pytest

from app.core.settings import BackendSettings
from app.perf.manager import PerfError, PerfManager

pytestmark = pytest.mark.unit


def _manager(tmp_path):
    # Minimal fake ModelManager.config with one group + instance.
    engine = SimpleNamespace(
        settings=SimpleNamespace(model_tag="Qwen/Qwen2.5-0.5B-Instruct"),
        instances=[SimpleNamespace(id="a", port=8006)],
    )
    emb = SimpleNamespace(
        port=8005,
        embedding_models={"m3e-base": SimpleNamespace(model_name="moka-ai/m3e-base")},
        reranking_models={"bge-reranker": SimpleNamespace(model_name="BAAI/bge-reranker-large")},
    )
    fake_mgr = SimpleNamespace(
        config=SimpleNamespace(LLM_engines={"Qwen2.5-0.5B": engine}, embedding_server=emb)
    )
    settings = BackendSettings(admin_token="adm")
    return PerfManager(None, fake_mgr, settings, str(tmp_path), "http://127.0.0.1:8887")


def test_build_cfg_router_target(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "Qwen2.5-0.5B", "target": "router", "dataset": "random",
         "parallel": [1, 4], "number": [10, 20], "max_tokens": 128},
        str(tmp_path / "1"),
    )
    assert cfg["url"] == "http://127.0.0.1:8887/v1/chat/completions"
    assert cfg["model"] == "Qwen2.5-0.5B"  # router routes by group key
    assert cfg["tokenizer_path"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert cfg["min_tokens"] == 128 and cfg["extra_args"] == {"ignore_eos": True}
    assert cfg["api_key"] == "adm"  # admin token avoids rate limiting


def test_build_cfg_instance_target_uses_port_and_tag(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "Qwen2.5-0.5B", "target": "instance", "instance_key": "Qwen2.5-0.5B::a",
         "endpoint": "completions", "dataset": "openqa", "parallel": [2], "number": [5]},
        str(tmp_path / "1"),
    )
    assert cfg["url"] == "http://127.0.0.1:8006/v1/completions"
    assert cfg["model"] == "Qwen/Qwen2.5-0.5B-Instruct"  # direct to vLLM
    assert "extra_args" not in cfg  # openqa: no random-specific knobs


def test_build_cfg_prefix_length(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "Qwen2.5-0.5B", "target": "router", "dataset": "random",
         "parallel": [4], "number": [10], "max_tokens": 128, "prefix_length": 256},
        str(tmp_path / "1"),
    )
    assert cfg["prefix_length"] == 256


def test_build_cfg_speed(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "Qwen2.5-0.5B", "mode": "speed", "target": "router", "max_tokens": 2048},
        str(tmp_path / "1"),
    )
    assert cfg["dataset"] == "speed_benchmark"
    assert cfg["url"].endswith("/v1/completions")  # not chat
    assert cfg["parallel"] == 1 and cfg["number"] == 8
    assert cfg["min_tokens"] == 2048 and cfg["max_tokens"] == 2048


def test_build_cfg_speed_long(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "Qwen2.5-0.5B", "mode": "speed", "speed_long": True},
        str(tmp_path / "1"),
    )
    assert cfg["dataset"] == "speed_benchmark_long" and cfg["number"] == 4


def test_build_cfg_multiturn_duration(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "Qwen2.5-0.5B", "mode": "multiturn", "mt_dataset": "random_multi_turn",
         "parallel": [4], "number": [20], "duration": 300, "max_tokens": 64},
        str(tmp_path / "1"),
    )
    assert cfg["duration"] == 300


def test_build_cfg_openloop(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "Qwen2.5-0.5B", "mode": "openloop", "dataset": "random",
         "rate": [5, 10], "number": [20, 40], "max_tokens": 64},
        str(tmp_path / "1"),
    )
    assert cfg["open_loop"] is True
    assert cfg["rate"] == [5, 10] and cfg["number"] == [20, 40]
    assert "parallel" not in cfg


def test_build_cfg_multiturn(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "Qwen2.5-0.5B", "mode": "multiturn", "mt_dataset": "share_gpt_zh_multi_turn",
         "parallel": [4], "number": [20], "min_turns": 2, "max_turns": 3, "max_tokens": 64},
        str(tmp_path / "1"),
    )
    assert cfg["multi_turn"] is True
    assert cfg["dataset"] == "share_gpt_zh_multi_turn"
    assert cfg["max_turns"] == 3 and cfg["parallel"] == [4]
    assert "extra_args" not in cfg  # multi-turn dataset, not "random"


def test_build_cfg_embedding(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "m3e-base", "mode": "embedding", "target": "router",
         "parallel": [1, 4], "number": [20, 40], "min_prompt_length": 32, "max_prompt_length": 128},
        str(tmp_path / "1"),
    )
    assert cfg["url"] == "http://127.0.0.1:8887/v1/embeddings"
    assert cfg["api"] == "openai_embedding" and cfg["dataset"] == "random_embedding"
    assert cfg["model"] == "m3e-base"  # embedding server routes by key
    assert cfg["tokenizer_path"] == "moka-ai/m3e-base"
    assert cfg["stream"] is False and cfg["parallel"] == [1, 4]
    assert "max_tokens" not in cfg  # embeddings have no output tokens
    assert cfg["api_key"] == "adm"


def test_build_cfg_rerank(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "bge-reranker", "mode": "rerank", "target": "instance",
         "parallel": [2], "number": [10], "rerank_documents": 20},
        str(tmp_path / "1"),
    )
    assert cfg["url"] == "http://127.0.0.1:8005/v1/rerank"  # direct to embedding server
    assert cfg["api"] == "llmops_rerank" and cfg["dataset"] == "random_rerank"
    assert cfg["tokenizer_path"] == "BAAI/bge-reranker-large"
    assert cfg["extra_args"] == {"num_documents": 20}


def test_resolve_unknown_embedding_model(tmp_path):
    pm = _manager(tmp_path)
    with pytest.raises(PerfError):
        pm._target({"model": "nope", "mode": "embedding", "target": "router"})


def test_resolve_unknown_group_and_instance(tmp_path):
    pm = _manager(tmp_path)
    with pytest.raises(PerfError):
        pm._resolve("nope", "router", None)
    with pytest.raises(PerfError):
        pm._resolve("Qwen2.5-0.5B", "instance", "Qwen2.5-0.5B::zzz")


def test_parse_result_extracts_points_and_p99(tmp_path):
    pm = _manager(tmp_path)
    data = {
        "parallel_4_number_20": {
            "metrics": {
                "concurrency": 4, "request_throughput": 12.5, "succeed_requests": 20,
                "failed_requests": 0, "avg_ttft": 80.0, "avg_tpot": 10.0,
                "output_token_throughput": 500.0, "avg_output_tokens": 128,
            },
            "percentiles": {"rows": [
                {"percentile": "50%", "ttft": 70, "latency": 1.0},
                {"percentile": "99%", "ttft": 150, "latency": 2.5},
            ]},
        },
        "parallel_1_number_10": {"metrics": {"concurrency": 1, "request_throughput": 5.0}, "percentiles": {}},
    }
    path = tmp_path / "result.json"
    # tolerate the legacy flat shape (no "points" wrapper)
    path.write_text(json.dumps(data), encoding="utf-8")
    parsed = pm._parse_result(str(path))
    points = parsed["points"]
    assert parsed["sla"] is None
    # sorted by concurrency ascending
    assert [p["concurrency"] for p in points] == [1, 4]
    hi = points[1]
    assert hi["rps"] == 12.5 and hi["success"] == 20
    assert hi["ttft_p99"] == 150 and hi["latency_p99"] == 2.5


def test_parse_result_new_shape_with_sla(tmp_path):
    pm = _manager(tmp_path)
    data = {
        "points": {"parallel_8": {"metrics": {"concurrency": 8, "request_throughput": 9.0}, "percentiles": {}}},
        "sla": [{"criteria": "p99_latency <= 2.0", "variable": "parallel", "max_satisfied": 8, "note": "Satisfied",
                 "points": [{"val": 8, "passed": True, "metrics": {"p99_latency": 0.6}}]}],
    }
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    parsed = pm._parse_result(str(path))
    assert parsed["points"][0]["concurrency"] == 8
    assert parsed["sla"][0]["max_satisfied"] == 8


def test_build_cfg_sla_mode(tmp_path):
    pm = _manager(tmp_path)
    cfg = pm._build_cfg(
        {"model": "Qwen2.5-0.5B", "mode": "sla", "dataset": "random",
         "sla_variable": "parallel", "sla_params": [{"p99_latency": "<=2"}],
         "sla_lower_bound": 1, "sla_upper_bound": 16, "max_tokens": 64},
        str(tmp_path / "1"),
    )
    assert cfg["sla_auto_tune"] is True
    assert cfg["sla_variable"] == "parallel"
    assert cfg["sla_params"] == [{"p99_latency": "<=2"}]
    assert cfg["sla_upper_bound"] == 16 and cfg["parallel"] == 1
    assert "number" in cfg  # placeholder; the tuner recomputes per point


# ---- cancel: non-blocking + force kill -------------------------------------

async def test_cancel_running_is_nonblocking_and_terminates(tmp_path, monkeypatch):
    import asyncio

    from app.perf import manager as perf_mod

    calls = []
    monkeypatch.setattr(perf_mod, "terminate_process_group", lambda proc: calls.append("term"))
    monkeypatch.setattr(perf_mod, "kill_process_group", lambda proc: calls.append("kill"))

    pm = _manager(tmp_path)
    pm._procs[7] = object()  # pretend a run is in flight
    assert await pm.cancel(7) is True
    assert 7 in pm._cancelled
    await asyncio.sleep(0.05)  # let the fire-and-forget executor run
    assert calls == ["term"]


async def test_cancel_force_uses_sigkill(tmp_path, monkeypatch):
    import asyncio

    from app.perf import manager as perf_mod

    calls = []
    monkeypatch.setattr(perf_mod, "terminate_process_group", lambda proc: calls.append("term"))
    monkeypatch.setattr(perf_mod, "kill_process_group", lambda proc: calls.append("kill"))

    pm = _manager(tmp_path)
    pm._procs[9] = object()
    assert await pm.cancel(9, force=True) is True
    await asyncio.sleep(0.05)
    assert calls == ["kill"]


async def test_cancel_unknown_run_returns_false(tmp_path):
    pm = _manager(tmp_path)
    assert await pm.cancel(123) is False

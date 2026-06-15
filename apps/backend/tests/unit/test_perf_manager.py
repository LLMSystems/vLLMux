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
    fake_mgr = SimpleNamespace(config=SimpleNamespace(LLM_engines={"Qwen2.5-0.5B": engine}))
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

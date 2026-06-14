import pytest

from src.llm_router.vllm_metrics_client import (VLLMInstanceMetrics,
                                                VLLMMetricsClient)

pytestmark = pytest.mark.unit


def test_compute_load_score_weights():
    m = VLLMInstanceMetrics("x", running=2, waiting=1, kv_cache_usage_perc=0.5)
    # waiting*10 + running*3 + kv*100 = 10 + 6 + 50
    assert m.compute_load_score() == 66.0


def test_parse_metrics_sums_labelled_lines_and_ignores_comments():
    client = VLLMMetricsClient(http_client=None)
    text = (
        "# HELP vllm:num_requests_running Running requests\n"
        "# TYPE vllm:num_requests_running gauge\n"
        'vllm:num_requests_running{model_name="a"} 2\n'
        'vllm:num_requests_running{model_name="b"} 3\n'
        "vllm:num_requests_waiting 1\n"
    )
    parsed = client.parse_metrics(text)
    assert parsed["vllm:num_requests_running"] == 5.0
    assert parsed["vllm:num_requests_waiting"] == 1.0


class _FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, text):
        self._text = text

    async def get(self, url, timeout=None):
        return _FakeResp(self._text)


async def test_fetch_builds_metrics_from_endpoint_text():
    text = "vllm:num_requests_running 4\nvllm:kv_cache_usage_perc 0.25\n"
    client = VLLMMetricsClient(http_client=_FakeClient(text))
    m = await client.fetch("http://localhost:8002")
    assert m.running == 4.0
    assert m.kv_cache_usage_perc == 0.25
    assert m.base_url == "http://localhost:8002"


async def test_safe_fetch_returns_infinite_load_on_error():
    class _BoomClient:
        async def get(self, url, timeout=None):
            raise ConnectionError("down")

    client = VLLMMetricsClient(http_client=_BoomClient())
    name, m = await client._safe_fetch("a", "http://localhost:8002")
    assert name == "a"
    assert m.running == float("inf")  # fail-open: looks maximally loaded
    assert m.kv_cache_usage_perc == float("inf")
    # The unreachable sentinel must not leak into the dashboard as a real value:
    # to_dict nulls it like running/waiting (a misleading 100% otherwise).
    d = m.to_dict()
    assert d["kv_cache_usage_perc"] is None
    assert d["running"] is None and d["waiting"] is None


async def test_to_dict_keeps_real_kv_cache_value():
    m = VLLMInstanceMetrics("x", running=1, waiting=0, kv_cache_usage_perc=0.42)
    assert m.to_dict()["kv_cache_usage_perc"] == 0.42

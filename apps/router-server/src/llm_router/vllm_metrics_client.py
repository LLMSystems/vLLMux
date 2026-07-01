import asyncio
import re
import math
from dataclasses import dataclass
from typing import Dict, Optional

import httpx


@dataclass
class VLLMInstanceMetrics:
    base_url: str
    running: float = 0.0
    waiting: float = 0.0
    kv_cache_usage_perc: float = 0.0
    prompt_tokens: float = 0.0
    generation_tokens: float = 0.0
    raw_metrics: Optional[str] = None
    # True when the backend has level-1/2 slept this instance (VRAM freed). Set by
    # the metrics poller for sleep-capable groups; routing skips sleeping instances.
    is_sleeping: bool = False
    
    def compute_load_score(
        self,
        waiting_weight: float = 10.0,
        running_weight: float = 3.0,
        kv_cache_weight: float = 100.0,
    ) -> float:
        """
        A simple heuristic score for load-aware routing.
        Lower score means the backend is less loaded.
        """
        return (
            self.waiting * waiting_weight
            + self.running * running_weight
            + self.kv_cache_usage_perc * kv_cache_weight
        )
        
    # to dict
    def to_dict(self):
        return {
            "base_url": self.base_url,
            "running": None if math.isinf(self.running) else self.running,
            "waiting": None if math.isinf(self.waiting) else self.waiting,
            # Null the unreachable sentinel like the other fields, so a failed
            # scrape shows "unknown" rather than a misleading 100% cache.
            "kv_cache_usage_perc": None if math.isinf(self.kv_cache_usage_perc) else self.kv_cache_usage_perc,
            "prompt_tokens": None if math.isinf(self.prompt_tokens) else self.prompt_tokens,
            "generation_tokens": None if math.isinf(self.generation_tokens) else self.generation_tokens,
            "is_sleeping": self.is_sleeping,
            "raw_metrics": self.raw_metrics,
        }
        
        
# Each engine exposes the same concepts under different Prometheus metric names.
# Parsing normalises both into the identical VLLMInstanceMetrics shape, so the
# downstream load-monitor / autoscaler / routing stay engine-agnostic. SGLang's
# token_usage (0..1 of the token pool) is the closest analog to vLLM's KV usage.
METRIC_NAMES_BY_ENGINE = {
    "vllm": {
        "running": "vllm:num_requests_running",
        "waiting": "vllm:num_requests_waiting",
        "kv_cache_usage_perc": "vllm:kv_cache_usage_perc",
        "prompt_tokens": "vllm:prompt_tokens",
        "generation_tokens": "vllm:generation_tokens",
    },
    "sglang": {
        "running": "sglang:num_running_reqs",
        "waiting": "sglang:num_queue_reqs",
        "kv_cache_usage_perc": "sglang:token_usage",
        "prompt_tokens": "sglang:prompt_tokens_total",
        "generation_tokens": "sglang:generation_tokens_total",
    },
    "llamacpp": {
        # llama.cpp exposes llamacpp:* with --metrics. It has no KV-cache-usage gauge
        # (verified live: the metric is absent), so kv_cache_usage_perc points at a
        # non-existent name and parse() falls back to 0.0 — the autoscaler scales
        # llamacpp groups on waiting/running only. See docs/llama_cpp_serve.md §D.
        "running": "llamacpp:requests_processing",
        "waiting": "llamacpp:requests_deferred",
        "kv_cache_usage_perc": "llamacpp:__no_kv_metric__",
        "prompt_tokens": "llamacpp:prompt_tokens_total",
        "generation_tokens": "llamacpp:tokens_predicted_total",
    },
}


class VLLMMetricsClient:
    # Default (vLLM) names; engine-specific lookups use METRIC_NAMES_BY_ENGINE.
    METRIC_NAMES = METRIC_NAMES_BY_ENGINE["vllm"]

    def __init__(self, http_client: httpx.AsyncClient, timeout: float = 2.0) -> None:
        self.http_client = http_client
        self.timeout = timeout

    async def fetch(self, base_url: str, engine: str = "vllm") -> Optional[VLLMInstanceMetrics]:
        metrics_url = base_url.rstrip("/") + "/metrics"

        resp = await self.http_client.get(metrics_url, timeout=self.timeout)
        resp.raise_for_status()

        parsed = self.parse_metrics(resp.text)
        names = METRIC_NAMES_BY_ENGINE.get(engine, self.METRIC_NAMES)

        return VLLMInstanceMetrics(
            base_url=base_url,
            running=parsed.get(names["running"], 0.0),
            waiting=parsed.get(names["waiting"], 0.0),
            kv_cache_usage_perc=parsed.get(names["kv_cache_usage_perc"], 0.0),
            prompt_tokens=parsed.get(names["prompt_tokens"], 0.0),
            generation_tokens=parsed.get(names["generation_tokens"], 0.0),
            raw_metrics=resp.text,
        )

    async def _safe_fetch(
        self,
        backend_name: str,
        base_url: str,
        engine: str = "vllm",
    ) -> tuple[str, VLLMInstanceMetrics]:
        try:
            metrics = await self.fetch(base_url, engine)
            return backend_name, metrics
        except Exception:
            return backend_name, VLLMInstanceMetrics(
                base_url=base_url,
                running=float("inf"),
                waiting=float("inf"),
                # inf (not 1.0) so it routes away AND to_dict nulls it for display.
                # compute_load_score still yields inf via running/waiting anyway.
                kv_cache_usage_perc=float("inf"),
                prompt_tokens=float("inf"),
                generation_tokens=float("inf"),
                raw_metrics=None,
            )
    
    async def fetch_many(
        self,
        backends: Dict[str, object],
    ) -> Dict[str, VLLMInstanceMetrics]:
        """
        Fetch metrics for many backends.

        Args:
            backends: mapping of name -> base_url, or name -> (base_url, engine) to
                scrape multi-engine fleets. A bare string defaults engine to "vllm":
                {
                    "qwen14b-a": "http://127.0.0.1:8001",
                    "sgl-b": ("http://127.0.0.1:8100", "sglang"),
                }

        Returns:
            Dict[str, VLLMInstanceMetrics]
        """
        tasks = []
        for backend_name, value in backends.items():
            base_url, engine = value if isinstance(value, tuple) else (value, "vllm")
            tasks.append(self._safe_fetch(backend_name, base_url, engine))

        pairs = await asyncio.gather(*tasks)
        return dict(pairs)

    def parse_metrics(self, text: str) -> Dict[str, float]:
        """
        Parse Prometheus text exposition format into a metric-name -> value mapping.

        Notes:
        - Ignores # HELP / # TYPE lines
        - Supports both:
            vllm:num_requests_running 2
            vllm:num_requests_running{model_name="..."} 2
        - If the same metric appears multiple times with different labels,
          values are summed.
        """
        values: Dict[str, float] = {}

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parsed = self._parse_metric_line(line)
            if parsed is None:
                continue

            metric_name, metric_value = parsed
            values[metric_name] = values.get(metric_name, 0.0) + metric_value

        return values

    def _parse_metric_line(self, line: str) -> Optional[tuple[str, float]]:
        """
        Parse one Prometheus metric line.

        Examples:
            vllm:num_requests_running 2
            vllm:num_requests_running{model_name="Qwen"} 2
        """
        pattern = r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)$'
        match = re.match(pattern, line)
        if not match:
            return None

        metric_name = match.group(1)
        metric_value = float(match.group(3))
        return metric_name, metric_value
    

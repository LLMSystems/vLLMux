"""In-memory manager for evalscope load-test runs.

Each run is an isolated subprocess (app.perf.runner) so evalscope's heavy deps
and its own asyncio loop stay out of the backend. Lifecycle + summary are
persisted to the shared store (`perf_runs`); raw evalscope output stays on disk.
One run at a time — a benchmark saturates the model and shouldn't overlap.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys

from app.llmops.process import terminate_process_group

logger = logging.getLogger(__name__)

# apps/backend/app/perf/manager.py -> apps/backend
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


class PerfBusy(RuntimeError):
    """A benchmark is already running (only one at a time)."""


class PerfError(RuntimeError):
    """Invalid benchmark request (bad model / target)."""


class PerfManager:
    def __init__(self, store, manager, settings, perf_root: str, router_url: str) -> None:
        self.store = store
        self.manager = manager  # ModelManager — for config (model_tag) + instance ports
        self.settings = settings
        self.perf_root = perf_root
        self.router_url = router_url.rstrip("/")
        self._current: int | None = None
        self._procs: dict[int, subprocess.Popen] = {}
        self._cancelled: set[int] = set()

    @property
    def busy(self) -> bool:
        return self._current is not None

    def _resolve(self, group: str, target: str, instance_key: str | None):
        """Return (model_field, url) for the benchmark, plus the tokenizer tag."""
        engine = self.manager.config.LLM_engines.get(group)
        if engine is None:
            raise PerfError(f"unknown model group: {group}")
        model_tag = engine.settings.model_tag

        if target == "instance":
            inst_id = (instance_key or "").split("::")[-1]
            inst = next((i for i in engine.instances if i.id == inst_id), None)
            if inst is None:
                raise PerfError(f"unknown instance: {instance_key}")
            base = f"http://127.0.0.1:{inst.port}"
            model_field = model_tag  # hitting vLLM directly: address by served tag
        else:
            base = self.router_url
            model_field = group  # router routes by group key
        return model_field, base, model_tag

    def _build_cfg(self, req: dict, run_dir: str) -> dict:
        group = req["model"]
        path = "/v1/completions" if req.get("endpoint") == "completions" else "/v1/chat/completions"
        model_field, base, model_tag = self._resolve(group, req.get("target", "router"), req.get("instance_key"))

        cfg: dict = {
            "model": model_field,
            "url": f"{base}{path}",
            "api": "openai",
            "dataset": req.get("dataset", "random"),
            "parallel": req["parallel"],
            "number": req["number"],
            "max_tokens": req.get("max_tokens", 256),
            "stream": req.get("stream", True),
            "tokenizer_path": model_tag,
            "outputs_dir": run_dir,
            "no_timestamp": True,
            "name": "run",
        }
        if cfg["dataset"] == "random":
            cfg.update({
                "min_tokens": cfg["max_tokens"],  # fixed output length for fair comparison
                "min_prompt_length": req.get("min_prompt_length", 512),
                "max_prompt_length": req.get("max_prompt_length", 512),
                "prefix_length": 0,
                "extra_args": {"ignore_eos": True},
            })
        if req.get("warmup_num"):
            cfg["warmup_num"] = req["warmup_num"]
        # Use the admin token so a benchmark isn't throttled by router rate limits.
        if getattr(self.settings, "admin_token", ""):
            cfg["api_key"] = self.settings.admin_token
        return cfg

    async def start(self, req: dict) -> dict:
        if self._current is not None:
            raise PerfBusy("a benchmark is already running")
        group = req["model"]
        # Validate target/model up front so we fail fast (raises PerfError).
        _, url, _ = self._resolve(group, req.get("target", "router"), req.get("instance_key"))
        path = "/v1/completions" if req.get("endpoint") == "completions" else "/v1/chat/completions"

        run_id = await self.store.create_perf_run(
            model=group, target_url=f"{url}{path}", params=json.dumps(req), name=req.get("name"),
        )
        run_dir = os.path.join(self.perf_root, str(run_id))
        os.makedirs(run_dir, exist_ok=True)
        cfg = self._build_cfg(req, run_dir)
        config_path = os.path.join(run_dir, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)

        log_file = open(os.path.join(run_dir, "run.log"), "w", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = _BACKEND_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.perf.runner", config_path, run_dir],
            cwd=_BACKEND_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_file.close()
        self._current = run_id
        self._procs[run_id] = proc
        asyncio.create_task(self._watch(run_id, proc, run_dir))
        logger.info("Started perf run %d (%s)", run_id, group)
        return await self.store.get_perf_run(run_id)

    async def _watch(self, run_id: int, proc: subprocess.Popen, run_dir: str) -> None:
        loop = asyncio.get_event_loop()
        rc = await loop.run_in_executor(None, proc.wait)
        self._procs.pop(run_id, None)
        if self._current == run_id:
            self._current = None

        result_path = os.path.join(run_dir, "result.json")
        if run_id in self._cancelled:
            self._cancelled.discard(run_id)
            await self.store.finish_perf_run(run_id, "cancelled", output_dir=run_dir)
            return
        if rc == 0 and os.path.exists(result_path):
            try:
                points = self._parse_result(result_path)
                await self.store.finish_perf_run(
                    run_id, "completed", result=json.dumps(points), output_dir=run_dir,
                )
                logger.info("Perf run %d completed (%d points)", run_id, len(points))
            except Exception as e:
                await self.store.finish_perf_run(
                    run_id, "failed", output_dir=run_dir, error=f"result parse error: {e}",
                )
        else:
            await self.store.finish_perf_run(
                run_id, "failed", output_dir=run_dir, error=f"runner exited rc={rc}; see run.log",
            )

    @staticmethod
    def _pct(percentiles: dict, *labels: str) -> dict:
        """Return the percentile row matching any of `labels` (e.g. '99%', 'max')."""
        wanted = {str(l).strip().lower() for l in labels}
        for row in (percentiles or {}).get("rows", []):
            if str(row.get("percentile", "")).strip().lower() in wanted:
                return row
        return {}

    def _parse_result(self, path: str) -> list[dict]:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        points: list[dict] = []
        for label, val in data.items():
            m = val.get("metrics") or {}
            pct = val.get("percentiles") or {}
            p50 = self._pct(pct, "50%", "p50", "50")
            p99 = self._pct(pct, "99%", "p99", "99")
            pmax = self._pct(pct, "max")
            points.append({
                "label": label,
                "concurrency": m.get("concurrency"),
                "rate": m.get("request_rate"),
                "total": m.get("total_requests"),
                "success": m.get("succeed_requests"),
                "failed": m.get("failed_requests"),
                "duration": m.get("time_taken"),
                "rps": m.get("request_throughput"),
                "avg_latency": m.get("avg_latency"),
                "avg_ttft": m.get("avg_ttft"),
                "avg_tpot": m.get("avg_tpot"),
                "avg_itl": m.get("avg_itl"),
                "output_tps": m.get("output_token_throughput"),
                "total_tps": m.get("total_token_throughput"),
                "avg_in": m.get("avg_input_tokens"),
                "avg_out": m.get("avg_output_tokens"),
                # per-metric percentiles (avg comes from the metrics block above)
                "latency_p50": p50.get("latency"), "latency_p99": p99.get("latency"), "latency_max": pmax.get("latency"),
                "ttft_p50": p50.get("ttft"), "ttft_p99": p99.get("ttft"), "ttft_max": pmax.get("ttft"),
                "tpot_p50": p50.get("tpot"), "tpot_p99": p99.get("tpot"), "tpot_max": pmax.get("tpot"),
            })
        points.sort(key=lambda p: (p.get("concurrency") or 0, p.get("rate") or 0))
        return points

    async def cancel(self, run_id: int) -> bool:
        proc = self._procs.get(run_id)
        if proc is None:
            return False
        self._cancelled.add(run_id)
        await asyncio.get_event_loop().run_in_executor(None, terminate_process_group, proc)
        return True

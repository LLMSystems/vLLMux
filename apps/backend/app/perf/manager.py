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

    def _lora_lookup(self, name: str):
        """Find a base group exposing a LoRA served as `name`. Returns
        (group_key, engine) or None."""
        for key, engine in self.manager.config.LLM_engines.items():
            for mod in (getattr(engine.settings, "lora_modules", None) or []):
                if getattr(mod, "name", None) == name:
                    return key, engine
        return None

    def _resolve(self, group: str, target: str, instance_key: str | None):
        """Return (model_field, url) for the benchmark, plus the tokenizer tag.

        `group` may be a base model group or a LoRA served name (routed over its
        base group's instances). The tokenizer always uses the base model_tag."""
        engine = self.manager.config.LLM_engines.get(group)
        is_lora = False
        if engine is None:
            found = self._lora_lookup(group)
            if found is None:
                raise PerfError(f"unknown model group: {group}")
            _, engine = found
            is_lora = True
        model_tag = engine.settings.model_tag

        if target == "instance":
            inst_id = (instance_key or "").split("::")[-1]
            inst = next((i for i in engine.instances if i.id == inst_id), None)
            if inst is None:
                raise PerfError(f"unknown instance: {instance_key}")
            base = f"http://127.0.0.1:{inst.port}"
            # vLLM serves a LoRA under its served name; a base model by its tag.
            model_field = group if is_lora else model_tag
        else:
            base = self.router_url
            model_field = group  # router routes by group key or LoRA served name
        return model_field, base, model_tag

    def _resolve_embedding(self, name: str, mode: str, target: str):
        """Resolve an embedding/rerank model to (model_field, base_url, tokenizer).

        Both modes share the /v1/embeddings endpoint; the embedding server routes
        by the model key (same field for router or direct). The tokenizer is the
        model's HF id (evalscope's random_* dataset needs it to synthesize text).
        """
        emb = getattr(self.manager.config, "embedding_server", None)
        field = "embedding_models" if mode == "embedding" else "reranking_models"
        models = getattr(emb, field, {}) if emb else {}
        entry = models.get(name)
        if entry is None:
            raise PerfError(f"unknown {mode} model: {name}")
        tokenizer = getattr(entry, "model_name", None) or name
        base = f"http://127.0.0.1:{emb.port}" if target == "instance" else self.router_url
        return name, base, tokenizer

    def _target(self, req: dict):
        """(model_field, full_url, tokenizer) for any mode — LLM or embedding/rerank."""
        mode = req.get("mode", "sweep")
        group = req["model"]
        target = req.get("target", "router")
        if mode in ("embedding", "rerank"):
            model_field, base, tokenizer = self._resolve_embedding(group, mode, target)
            return model_field, f"{base}/v1/embeddings", tokenizer
        model_field, base, model_tag = self._resolve(group, target, req.get("instance_key"))
        path = "/v1/completions" if req.get("endpoint") == "completions" else "/v1/chat/completions"
        return model_field, f"{base}{path}", model_tag

    def _build_embedding_cfg(self, req: dict, run_dir: str, model_field: str, url: str, tokenizer: str) -> dict:
        """Closed-loop concurrency sweep for embedding/rerank (non-streaming, no
        output tokens). Uses evalscope's random_* dataset + our matching plugin."""
        is_rerank = req.get("mode") == "rerank"
        cfg: dict = {
            "model": model_field,
            "url": url,
            "api": "llmops_rerank" if is_rerank else "openai_embedding",
            "dataset": "random_rerank" if is_rerank else "random_embedding",
            "tokenizer_path": tokenizer,
            "stream": False,
            "parallel": req["parallel"],
            "number": req["number"],
            "min_prompt_length": req.get("min_prompt_length", 64),
            "max_prompt_length": req.get("max_prompt_length", 256),
            "outputs_dir": run_dir,
            "no_timestamp": True,
            "name": "run",
        }
        if is_rerank:
            cfg["extra_args"] = {"num_documents": req.get("rerank_documents", 10)}
        if getattr(self.settings, "admin_token", ""):
            cfg["api_key"] = self.settings.admin_token
        return cfg

    def _build_speed_cfg(self, req: dict, run_dir: str, model_field: str, url: str, tokenizer: str) -> dict:
        """Single-request speed baseline (evalscope `speed_benchmark`). Self-contained
        dataset (no download), fixed prompt lengths, fixed 2048-token output, hits
        /v1/completions (chat templates would skew the numbers)."""
        out = req.get("max_tokens", 2048)
        cfg: dict = {
            "model": model_field,
            "url": url.replace("/v1/chat/completions", "/v1/completions"),
            "api": "openai",
            "dataset": "speed_benchmark_long" if req.get("speed_long") else "speed_benchmark",
            "tokenizer_path": tokenizer,
            "parallel": 1,
            "number": 4 if req.get("speed_long") else 8,  # lengths × 2 repeats
            "min_tokens": out,
            "max_tokens": out,
            "stream": True,
            "outputs_dir": run_dir,
            "no_timestamp": True,
            "name": "run",
        }
        if getattr(self.settings, "admin_token", ""):
            cfg["api_key"] = self.settings.admin_token
        return cfg

    def _build_cfg(self, req: dict, run_dir: str) -> dict:
        mode = req.get("mode", "sweep")
        model_field, url, tokenizer = self._target(req)

        if mode in ("embedding", "rerank"):
            return self._build_embedding_cfg(req, run_dir, model_field, url, tokenizer)
        if mode == "speed":
            return self._build_speed_cfg(req, run_dir, model_field, url, tokenizer)

        cfg: dict = {
            "model": model_field,
            "url": url,
            "api": "openai",
            "dataset": req.get("dataset", "random"),
            "max_tokens": req.get("max_tokens", 256),
            "stream": req.get("stream", True),
            "tokenizer_path": tokenizer,
            "outputs_dir": run_dir,
            "no_timestamp": True,
            "name": "run",
        }
        def _random_knobs():
            if cfg["dataset"] == "random":
                cfg.update({
                    "min_tokens": cfg["max_tokens"],  # fixed output length for fair comparison
                    "min_prompt_length": req.get("min_prompt_length", 512),
                    "max_prompt_length": req.get("max_prompt_length", 512),
                    # Shared prefix across requests in a point — raise to observe prefix-cache gains.
                    "prefix_length": req.get("prefix_length", 0),
                    "extra_args": {"ignore_eos": True},
                })

        mode = req.get("mode", "sweep")
        if mode == "sla":
            _random_knobs()
            variable = req.get("sla_variable", "parallel")
            lo = req.get("sla_lower_bound", 1)
            cfg.update({
                "sla_auto_tune": True,
                "sla_variable": variable,
                "sla_params": req["sla_params"],
                "sla_lower_bound": lo,
                "sla_upper_bound": req.get("sla_upper_bound", 64),
                "sla_num_runs": req.get("sla_num_runs", 1),
                "number": 1,  # the tuner recomputes per search point
            })
            if variable == "rate":
                cfg["rate"] = lo
                cfg["sla_fixed_parallel"] = req.get("sla_fixed_parallel", 10)
            else:
                cfg["parallel"] = lo
        elif mode == "openloop":
            _random_knobs()
            cfg.update({"open_loop": True, "rate": req["rate"], "number": req["number"]})
            if req.get("duration"):
                cfg["duration"] = req["duration"]
        elif mode == "multiturn":
            cfg.update({
                "multi_turn": True,
                "dataset": req.get("mt_dataset", "share_gpt_zh_multi_turn"),  # real bounded chats
                "min_turns": req.get("min_turns", 2),
                "max_turns": req.get("max_turns", 4),
                "parallel": req["parallel"],
                "number": req["number"],
            })
            if req.get("mt_dataset_path"):
                cfg["dataset_path"] = req["mt_dataset_path"]
            if req.get("duration"):  # soft exit: stop starting new conversations after N seconds
                cfg["duration"] = req["duration"]
        else:  # sweep (closed-loop)
            _random_knobs()
            cfg["parallel"] = req["parallel"]
            cfg["number"] = req["number"]
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
        _, url, _ = self._target(req)

        run_id = await self.store.create_perf_run(
            model=group, target_url=url, params=json.dumps(req), name=req.get("name"),
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
                parsed = self._parse_result(result_path)
                await self.store.finish_perf_run(
                    run_id, "completed", result=json.dumps(parsed), output_dir=run_dir,
                )
                logger.info("Perf run %d completed (%d points)", run_id, len(parsed["points"]))
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

    def _parse_result(self, path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # New runner format: {"points": {...}, "sla": [...]}; tolerate the old flat shape.
        raw = data.get("points", data) if isinstance(data, dict) else {}
        sla = data.get("sla") if isinstance(data, dict) else None
        points: list[dict] = []
        for label, val in raw.items():
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
                # multi-turn only (None otherwise)
                "turns": m.get("avg_turns"),
                "cache_hit": m.get("avg_cached_percent"),
                "first_ttft": m.get("avg_first_turn_ttft"),
                "subsequent_ttft": m.get("avg_subsequent_turn_ttft"),
            })
        points.sort(key=lambda p: (p.get("concurrency") or 0, p.get("rate") or 0))
        return {"points": points, "sla": sla}

    async def cancel(self, run_id: int) -> bool:
        proc = self._procs.get(run_id)
        if proc is None:
            return False
        self._cancelled.add(run_id)
        await asyncio.get_event_loop().run_in_executor(None, terminate_process_group, proc)
        return True

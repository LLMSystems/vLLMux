"""In-memory manager for evalscope accuracy/quality eval runs.

Mirrors app.perf.manager.PerfManager: each run is an isolated subprocess
(app.eval.runner) so evalscope's heavy deps + asyncio loop stay out of the
backend. Lifecycle + per-dataset scores are persisted to the shared store
(`eval_runs`); raw evalscope output (predictions/reviews/reports/HTML) stays on
disk. One run at a time — an eval saturates the model and shouldn't overlap (and
must not overlap a load test either, but that guard lives at the API layer).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys

from app.llmops.process import terminate_process_group

logger = logging.getLogger(__name__)

# apps/backend/app/eval/manager.py -> apps/backend
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


class EvalBusy(RuntimeError):
    """Kept for backwards compatibility; no longer raised (evals now queue)."""


class EvalError(RuntimeError):
    """Invalid eval request (bad model / target / datasets)."""


class EvalManager:
    def __init__(self, store, manager, settings, eval_root: str, router_url: str) -> None:
        self.store = store
        self.manager = manager  # ModelManager — for config (model_tag) + instance ports
        self.settings = settings
        self.eval_root = eval_root
        self.router_url = router_url.rstrip("/")
        # Evals run in parallel within a shared batch_size budget; the rest queue.
        self.concurrency_budget: int = getattr(settings, "eval_concurrency_budget", 32)
        # Set by main.py after construction — used to keep evals off the GPU while
        # a load test owns it (and vice-versa).
        self.perf_manager = None
        self._procs: dict[int, subprocess.Popen] = {}  # run_id -> proc (running)
        self._queued: list[int] = []                   # FIFO of waiting run_ids
        self._pending: dict[int, dict] = {}            # run_id -> req (until launched)
        self._batch: dict[int, int] = {}               # run_id -> eval_batch_size
        self._cancelled: set[int] = set()

    @property
    def busy(self) -> bool:
        """Any eval running or queued — used by the load-test mutex."""
        return bool(self._procs or self._queued)

    @property
    def used_budget(self) -> int:
        """Sum of batch_size across currently running evals."""
        return sum(self._batch.get(rid, 0) for rid in self._procs)

    def _perf_busy(self) -> bool:
        return bool(self.perf_manager is not None and self.perf_manager.busy)

    def _lora_lookup(self, name: str):
        """Find a base group exposing a LoRA served as `name`. Returns
        (group_key, engine) or None."""
        for key, engine in self.manager.config.LLM_engines.items():
            for mod in (getattr(engine.settings, "lora_modules", None) or []):
                if getattr(mod, "name", None) == name:
                    return key, engine
        return None

    def _resolve(self, group: str, target: str, instance_key: str | None):
        """Return (model_field, openai_base_url) for the eval.

        `group` may be a base model group or a LoRA served name. Router target
        addresses the model by that name as-is (the router routes LoRAs over the
        base group); hitting a vLLM instance directly addresses a base model by
        its model_tag, or a LoRA by its served name (vLLM serves it under it).
        """
        engine = self.manager.config.LLM_engines.get(group)
        is_lora = False
        if engine is None:
            found = self._lora_lookup(group)
            if found is None:
                raise EvalError(f"unknown model group: {group}")
            _, engine = found
            is_lora = True
        model_tag = engine.settings.model_tag

        if target == "instance":
            inst_id = (instance_key or "").split("::")[-1]
            inst = next((i for i in engine.instances if i.id == inst_id), None)
            if inst is None:
                raise EvalError(f"unknown instance: {instance_key}")
            base = f"http://127.0.0.1:{inst.port}"
            # A LoRA is served by the instance under its served name, not the tag.
            model_field = group if is_lora else model_tag
        else:
            base = self.router_url
            model_field = group
        return model_field, f"{base}/v1"

    def _build_cfg(self, req: dict, run_dir: str) -> dict:
        model_field, api_url = self._resolve(
            req["model"], req.get("target", "router"), req.get("instance_key")
        )
        gen: dict = {
            "temperature": req.get("temperature", 0.0),
            "max_tokens": req.get("max_tokens", 2048),
        }
        if req.get("top_p") is not None:
            gen["top_p"] = req["top_p"]

        cfg: dict = {
            "model": model_field,
            "api_url": api_url,
            "api_key": getattr(self.settings, "admin_token", "") or "EMPTY",
            "eval_type": "openai_api",
            "datasets": req["datasets"],
            "generation_config": gen,
            "repeats": req.get("repeats", 1),
            "stream": req.get("stream", True),
            "eval_batch_size": req.get("eval_batch_size", 8),
            "ignore_errors": True,  # a few bad samples shouldn't void the whole run
            "timeout": req.get("timeout", 600),
            "work_dir": run_dir,
            "no_timestamp": True,
        }
        if req.get("limit") is not None:
            cfg["limit"] = req["limit"]
        if req.get("dataset_args"):
            cfg["dataset_args"] = req["dataset_args"]
        self._apply_judge(req, cfg)
        return cfg

    def _apply_judge(self, req: dict, cfg: dict) -> None:
        """Wire an LLM judge for free-form QA datasets. The judge is either one of
        our deployed models (via the router) or an external OpenAI-compatible API.
        Disabled -> 'rule' strategy so rule-scored datasets never call a judge."""
        if not req.get("judge_enabled"):
            cfg["judge_strategy"] = "rule"
            return
        cfg["judge_strategy"] = req.get("judge_strategy", "auto")
        # Cap the judge's output length. evalscope's LLMJudge otherwise defaults to
        # max_tokens=4096, which overflows a small-context model (e.g. a prompt of
        # ~900 tokens + 4096 > a 5000-token window -> every grading call 400s and
        # the dataset silently scores 0). Reuse the eval's own budget.
        gen = {"max_tokens": req.get("max_tokens", 2048), "temperature": 0.0}
        if req.get("judge_target") == "external":
            cfg["judge_model_args"] = {
                "model_id": req["judge_model"],
                "api_url": req["judge_api_url"],
                "api_key": req.get("judge_api_key") or "EMPTY",
                "generation_config": gen,
            }
        else:  # internal: judge through our own router (eval_type defaults to openai_api)
            cfg["judge_model_args"] = {
                "model_id": req["judge_model"],
                "api_url": f"{self.router_url}/v1",
                "api_key": getattr(self.settings, "admin_token", "") or "EMPTY",
                "generation_config": gen,
            }

    async def start(self, req: dict) -> dict:
        """Create a run (queued) and try to launch it. Returns the run row, which
        will be 'running' if a concurrency slot was free or 'queued' otherwise."""
        if not req.get("datasets"):
            raise EvalError("at least one dataset is required")
        group = req["model"]
        # Validate target/model up front so we fail fast (raises EvalError).
        _, api_url = self._resolve(group, req.get("target", "router"), req.get("instance_key"))

        run_id = await self.store.create_eval_run(
            model=group, target_url=api_url, datasets=json.dumps(req["datasets"]),
            params=json.dumps(req), name=req.get("name"), status="queued",
        )
        self._pending[run_id] = req
        self._batch[run_id] = int(req.get("eval_batch_size", 8))
        self._queued.append(run_id)
        logger.info("Queued eval run %d (%s: %s)", run_id, group, req["datasets"])
        await self._pump()
        return await self.store.get_eval_run(run_id)

    async def _pump(self) -> None:
        """Launch queued runs in FIFO order while the batch_size budget allows and
        no load test is running. A run is admitted if either nothing is running
        (so a lone run larger than the budget still proceeds) or it fits the
        remaining budget."""
        while self._queued and not self._perf_busy():
            run_id = self._queued[0]
            batch = self._batch.get(run_id, 8)
            if self._procs and self.used_budget + batch > self.concurrency_budget:
                break  # head doesn't fit; FIFO — don't skip ahead
            self._queued.pop(0)
            await self._launch(run_id)

    async def _launch(self, run_id: int) -> None:
        req = self._pending.pop(run_id, None)
        if req is None:  # cancelled out of the queue before launch
            return
        run_dir = os.path.join(self.eval_root, str(run_id))
        os.makedirs(run_dir, exist_ok=True)
        cfg = self._build_cfg(req, run_dir)
        config_path = os.path.join(run_dir, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)

        log_file = open(os.path.join(run_dir, "run.log"), "w", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = _BACKEND_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"  # flush stdout live so the log tail isn't frozen
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.eval.runner", config_path, run_dir],
            cwd=_BACKEND_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_file.close()
        self._procs[run_id] = proc
        await self.store.start_eval_run(run_id)
        asyncio.create_task(self._watch(run_id, proc, run_dir))
        logger.info("Started eval run %d (%s: %s)", run_id, req["model"], req["datasets"])

    async def _finish(self, run_id: int, status: str, **kwargs) -> None:
        """Persist a run's terminal state, retrying transient DB-lock errors so a
        finished run is never left stuck in 'running'. Never raises."""
        for attempt in range(6):
            try:
                await self.store.finish_eval_run(run_id, status, **kwargs)
                return
            except Exception as e:  # noqa: BLE001 — last-resort durability
                if attempt == 5:
                    logger.error("Failed to persist eval run %d as %s: %s", run_id, status, e)
                    return
                await asyncio.sleep(0.5 * (attempt + 1))

    async def _watch(self, run_id: int, proc: subprocess.Popen, run_dir: str) -> None:
        loop = asyncio.get_event_loop()
        rc = await loop.run_in_executor(None, proc.wait)
        self._procs.pop(run_id, None)
        self._batch.pop(run_id, None)
        try:
            result_path = os.path.join(run_dir, "result.json")
            if run_id in self._cancelled:
                self._cancelled.discard(run_id)
                await self._finish(run_id, "cancelled", output_dir=run_dir)
                return
            if rc == 0 and os.path.exists(result_path):
                try:
                    with open(result_path, encoding="utf-8") as f:
                        parsed = json.load(f)
                except Exception as e:  # result.json unreadable/corrupt
                    await self._finish(run_id, "failed", output_dir=run_dir, error=f"result parse error: {e}")
                else:
                    await self._finish(
                        run_id, "completed", result=json.dumps(parsed), output_dir=run_dir,
                    )
                    logger.info("Eval run %d completed (%d datasets)", run_id, len(parsed.get("datasets", [])))
            else:
                detail = self._extract_error(run_dir)
                msg = f"runner exited rc={rc}" + (f": {detail}" if detail else "; see run.log")
                await self._finish(run_id, "failed", output_dir=run_dir, error=msg)
        finally:
            await self._pump()  # a slot freed up — admit the next queued run

    @staticmethod
    def _extract_error(run_dir: str, max_len: int = 400) -> str | None:
        """Pull the most informative error line out of the runner log so the UI can
        show the real cause (e.g. a vLLM 400 / context-length / missing-dep message)
        instead of a bare exit code."""
        path = os.path.join(run_dir, "run.log")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return None
        # Prefer specific, actionable signatures; fall back to any ERROR/exception.
        keys = ("Error code:", "BadRequestError", "maximum context length",
                "ImportError", "RuntimeError", "ValueError", "requires sandbox",
                "ERROR", "Exception")
        for key in keys:
            for ln in reversed(lines):
                if key in ln:
                    # Strip ANSI colour + log prefix noise, collapse whitespace.
                    clean = re.sub(r"\x1b\[[0-9;]*m", "", ln).strip()
                    clean = re.sub(r"^.*?(ERROR|WARNING)[^:]*:\s*", "", clean) or clean
                    clean = " ".join(clean.split())
                    if clean:
                        return clean[:max_len]
        return None

    async def cancel(self, run_id: int) -> bool:
        # Still queued (no process yet): drop it from the queue and mark cancelled.
        if run_id in self._queued:
            self._queued.remove(run_id)
            self._pending.pop(run_id, None)
            self._batch.pop(run_id, None)
            await self.store.finish_eval_run(run_id, "cancelled")
            return True
        proc = self._procs.get(run_id)
        if proc is None:
            return False
        self._cancelled.add(run_id)
        await asyncio.get_event_loop().run_in_executor(None, terminate_process_group, proc)
        return True

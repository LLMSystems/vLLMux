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
    """An eval is already running (only one at a time)."""


class EvalError(RuntimeError):
    """Invalid eval request (bad model / target / datasets)."""


class EvalManager:
    def __init__(self, store, manager, settings, eval_root: str, router_url: str) -> None:
        self.store = store
        self.manager = manager  # ModelManager — for config (model_tag) + instance ports
        self.settings = settings
        self.eval_root = eval_root
        self.router_url = router_url.rstrip("/")
        self._current: int | None = None
        self._procs: dict[int, subprocess.Popen] = {}
        self._cancelled: set[int] = set()

    @property
    def busy(self) -> bool:
        return self._current is not None

    def _resolve(self, group: str, target: str, instance_key: str | None):
        """Return (model_field, openai_base_url) for the eval.

        Router target addresses the model by its group key; hitting a vLLM
        instance directly addresses it by the served model_tag.
        """
        engine = self.manager.config.LLM_engines.get(group)
        if engine is None:
            raise EvalError(f"unknown model group: {group}")
        model_tag = engine.settings.model_tag

        if target == "instance":
            inst_id = (instance_key or "").split("::")[-1]
            inst = next((i for i in engine.instances if i.id == inst_id), None)
            if inst is None:
                raise EvalError(f"unknown instance: {instance_key}")
            base = f"http://127.0.0.1:{inst.port}"
            model_field = model_tag
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
        if self._current is not None:
            raise EvalBusy("an eval is already running")
        if not req.get("datasets"):
            raise EvalError("at least one dataset is required")
        group = req["model"]
        # Validate target/model up front so we fail fast (raises EvalError).
        _, api_url = self._resolve(group, req.get("target", "router"), req.get("instance_key"))

        run_id = await self.store.create_eval_run(
            model=group, target_url=api_url, datasets=json.dumps(req["datasets"]),
            params=json.dumps(req), name=req.get("name"),
        )
        run_dir = os.path.join(self.eval_root, str(run_id))
        os.makedirs(run_dir, exist_ok=True)
        cfg = self._build_cfg(req, run_dir)
        config_path = os.path.join(run_dir, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)

        log_file = open(os.path.join(run_dir, "run.log"), "w", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = _BACKEND_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.eval.runner", config_path, run_dir],
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
        logger.info("Started eval run %d (%s: %s)", run_id, group, req["datasets"])
        return await self.store.get_eval_run(run_id)

    async def _watch(self, run_id: int, proc: subprocess.Popen, run_dir: str) -> None:
        loop = asyncio.get_event_loop()
        rc = await loop.run_in_executor(None, proc.wait)
        self._procs.pop(run_id, None)
        if self._current == run_id:
            self._current = None

        result_path = os.path.join(run_dir, "result.json")
        if run_id in self._cancelled:
            self._cancelled.discard(run_id)
            await self.store.finish_eval_run(run_id, "cancelled", output_dir=run_dir)
            return
        if rc == 0 and os.path.exists(result_path):
            try:
                with open(result_path, encoding="utf-8") as f:
                    parsed = json.load(f)
                await self.store.finish_eval_run(
                    run_id, "completed", result=json.dumps(parsed), output_dir=run_dir,
                )
                logger.info("Eval run %d completed (%d datasets)", run_id, len(parsed.get("datasets", [])))
            except Exception as e:
                await self.store.finish_eval_run(
                    run_id, "failed", output_dir=run_dir, error=f"result parse error: {e}",
                )
        else:
            detail = self._extract_error(run_dir)
            msg = f"runner exited rc={rc}" + (f": {detail}" if detail else "; see run.log")
            await self.store.finish_eval_run(run_id, "failed", output_dir=run_dir, error=msg)

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
        proc = self._procs.get(run_id)
        if proc is None:
            return False
        self._cancelled.add(run_id)
        await asyncio.get_event_loop().run_in_executor(None, terminate_process_group, proc)
        return True

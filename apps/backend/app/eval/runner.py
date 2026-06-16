"""Standalone evalscope accuracy/quality runner — a subprocess of EvalManager.

Usage:
    python -m app.eval.runner <config.json> <run_dir>

Reads a TaskConfig (JSON), runs ``run_task`` against the served model, and
writes a normalized summary to ``<run_dir>/result.json``:

    {"datasets": [
        {"dataset": "gsm8k", "pretty": "GSM8K", "score": 0.42, "num": 5,
         "metrics": [{"name": "acc", "score": 0.42, "num": 5}]},
        ...]}

evalscope drops one report JSON per dataset under
``<run_dir>/reports/<model>/<dataset>.json``; we parse those (stable schema via
``Report``) rather than the in-memory return value. Imports only evalscope so
its heavy deps / asyncio loop stay out of the backend. Same isolation discipline
as app.perf.runner.
"""
import glob
import json
import os
import sys


def _collect_reports(run_dir: str) -> list:
    """Parse every evalscope report JSON under run_dir into compact per-dataset rows."""
    from evalscope.report import Report

    out: list = []
    for path in sorted(glob.glob(os.path.join(run_dir, "reports", "*", "*.json"))):
        try:
            rep = Report.from_json(path)
        except Exception as e:  # malformed / partial — skip but leave a breadcrumb
            print(f"[runner] skip unreadable report {path}: {e}", flush=True)
            continue
        out.append({
            "dataset": rep.dataset_name,
            "pretty": rep.dataset_pretty_name or rep.dataset_name,
            "score": rep.score,
            "num": rep.num,
            "metrics": [
                {"name": m.name, "score": m.score, "macro_score": m.macro_score, "num": m.num}
                for m in rep.metrics
            ],
        })
    return out


def main() -> int:
    config_path, run_dir = sys.argv[1], sys.argv[2]
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    from evalscope.config import TaskConfig
    from evalscope.run import run_task

    from app.runners_common import heartbeat

    # evalscope logs nothing during its prediction loop; the heartbeat keeps the
    # streamed run.log alive (elapsed + samples done) so the UI shows progress.
    with heartbeat(run_dir):
        run_task(task_cfg=TaskConfig(**cfg))

    datasets = _collect_reports(run_dir)
    out_path = os.path.join(run_dir, "result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"datasets": datasets}, f, ensure_ascii=False, default=str)
    print(f"[runner] wrote {out_path}: {len(datasets)} dataset(s)", flush=True)
    # No reports at all means every dataset errored out (e.g. all samples failed,
    # or a dataset needs infra we don't provide). Surface it as a failed run rather
    # than a hollow "completed" with an empty table — see run.log for the cause.
    if not datasets:
        print("[runner] no reports produced — all datasets failed; see errors above", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)

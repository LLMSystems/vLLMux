"""Shared stdlib-only helpers for the eval/perf runner subprocesses.

Deliberately dependency-free (no evalscope / backend imports) so it adds nothing
heavy to the runner processes.
"""
from __future__ import annotations

import glob
import os
import threading
import time
from contextlib import contextmanager


@contextmanager
def heartbeat(run_dir: str, interval: float = 5.0, count_glob: str | None = "predictions/*/*.jsonl"):
    """Print a periodic liveness line to stdout while a long run executes.

    Tools like evalscope log nothing to stdout during their prediction loop, so
    the streamed run.log looks frozen and the user can't tell "running" from
    "stuck". This emits `[runner] running… Ns elapsed` every `interval` seconds,
    plus a completed-row count derived from the tool's incremental output files
    (e.g. predictions/<model>/<dataset>.jsonl) when `count_glob` is set.
    """
    stop = threading.Event()
    started = time.time()

    def loop() -> None:
        while not stop.wait(interval):
            n = 0
            if count_glob:
                for path in glob.glob(os.path.join(run_dir, count_glob)):
                    try:
                        with open(path, "rb") as f:
                            n += sum(1 for _ in f)
                    except OSError:
                        pass
            msg = f"[runner] running… {int(time.time() - started)}s elapsed"
            if n:
                msg += f", {n} samples completed"
            print(msg, flush=True)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()

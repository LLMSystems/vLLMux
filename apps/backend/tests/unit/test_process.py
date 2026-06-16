"""OS-level process-group kill helpers (real short-lived subprocess)."""
import subprocess
import sys
import time

import pytest

from app.llmops.process import kill_process_group, terminate_process_group

pytestmark = pytest.mark.unit


def _spawn_sleeper() -> subprocess.Popen:
    # Own session/process group so the kill helpers can target the whole group.
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            start_new_session=True)


def test_kill_process_group_sigkills_immediately():
    proc = _spawn_sleeper()
    assert proc.poll() is None
    kill_process_group(proc)
    assert proc.poll() is not None  # reaped


def test_kill_process_group_noop_on_dead_proc():
    proc = _spawn_sleeper()
    kill_process_group(proc)
    kill_process_group(proc)  # second call must not raise


def test_terminate_process_group_graceful():
    proc = _spawn_sleeper()
    start = time.perf_counter()
    terminate_process_group(proc, timeout=5.0)
    # SIGTERM ends `sleep` well under the grace period.
    assert proc.poll() is not None
    assert time.perf_counter() - start < 5.0

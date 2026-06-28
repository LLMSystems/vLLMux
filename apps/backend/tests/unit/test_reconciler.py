import time

import pytest

from app.core.settings import BackendSettings
from app.llmops.launchers import EmbeddingLauncher, VllmLauncher
from app.llmops.manager import build_registry
from app.llmops.reconciler import adopt_running, reconcile_once
from app.llmops.state import Desired, ModelState
from tests.conftest import FAKE_CONFIG, FakeHTTPClient, FakeProc

pytestmark = pytest.mark.unit

HEALTHY = "Qwen3-0.6B::qwen3"      # port 8002 (healthy in FakeHTTPClient)
UNHEALTHY = "Qwen3-0.6B::qwen3-2"  # port 8004 (never answers)


@pytest.fixture(autouse=True)
def _no_real_kill(monkeypatch):
    # The startup-timeout path reaps the process group; never send real signals.
    import app.llmops.reconciler as rec

    monkeypatch.setattr(rec, "terminate_process_group", lambda proc, timeout=10.0: None)


def _registry():
    return build_registry(FAKE_CONFIG, "config.yaml", [VllmLauncher(), EmbeddingLauncher()])


def _settings():
    return BackendSettings()


async def test_starting_becomes_ready_when_health_ok():
    reg = _registry()
    inst = reg.get(HEALTHY)
    inst.state = ModelState.STARTING
    inst.managed = True
    inst.proc = FakeProc()
    inst.started_at = time.time()

    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings())
    assert inst.state == ModelState.READY
    assert inst.ready_at is not None


class _ReloadSpyManager:
    """Minimal manager stub capturing router-reload + Prometheus SD nudges."""

    def __init__(self):
        self.reloads = 0
        self.sd_writes = 0

    async def trigger_router_reload(self):
        self.reloads += 1
        return True

    async def write_prometheus_targets(self):
        self.sd_writes += 1
        return True


async def test_ready_transition_nudges_router_reload():
    reg = _registry()
    inst = reg.get(HEALTHY)
    inst.state = ModelState.STARTING
    inst.managed = True
    inst.proc = FakeProc()
    inst.started_at = time.time()

    mgr = _ReloadSpyManager()
    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings(), manager=mgr)
    assert inst.state == ModelState.READY
    assert mgr.reloads == 1
    assert mgr.sd_writes == 1  # joining the ready pool refreshes scrape targets


async def test_no_ready_transition_does_not_reload():
    # Steady-state pass (nothing turns READY) must not spam the router or rewrite SD.
    reg = _registry()
    mgr = _ReloadSpyManager()
    await reconcile_once(reg, FakeHTTPClient(healthy_ports=set()), _settings(), manager=mgr)
    assert mgr.reloads == 0
    assert mgr.sd_writes == 0


async def test_ready_to_failed_refreshes_sd_but_not_router():
    # A ready vLLM dying leaves the pool: SD must be rewritten (drop the target),
    # but the router reload only fires on instances *joining* the pool.
    reg = _registry()
    inst = reg.get(HEALTHY)
    inst.state = ModelState.READY
    inst.managed = True
    inst.proc = FakeProc(returncode=139)  # crashed

    mgr = _ReloadSpyManager()
    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings(), manager=mgr)
    assert inst.state == ModelState.FAILED
    assert mgr.sd_writes == 1
    assert mgr.reloads == 0


async def test_starting_times_out_to_failed():
    reg = _registry()
    inst = reg.get(UNHEALTHY)
    inst.state = ModelState.STARTING
    inst.managed = True
    inst.proc = FakeProc()
    inst.log_path = None  # nothing to observe -> no progress
    inst.started_at = time.time() - 10_000  # well past start_timeout
    inst.last_progress_at = time.time() - 10_000  # and no log progress since

    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings())
    assert inst.state == ModelState.FAILED
    assert "timeout" in inst.last_error
    assert inst.proc is None  # hung process reaped — no orphan


async def test_starting_with_log_progress_does_not_time_out(tmp_path):
    # A growing log = still downloading/loading; must NOT be mistaken for a hang
    # even when total elapsed exceeds start_timeout.
    reg = _registry()
    inst = reg.get(UNHEALTHY)
    inst.state = ModelState.STARTING
    inst.managed = True
    inst.proc = FakeProc()
    log = tmp_path / "run.log"
    log.write_text("downloading weights 42%\n", encoding="utf-8")
    inst.log_path = str(log)
    inst.last_log_size = 0  # not yet observed -> this tick sees growth
    inst.started_at = time.time() - 10_000
    inst.last_progress_at = time.time() - 10_000

    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings())
    assert inst.state == ModelState.STARTING  # progress detected -> keep waiting
    assert inst.last_progress_at > time.time() - 5


async def test_starting_timeout_schedules_restart(tmp_path):
    reg = _registry()
    inst = reg.get(UNHEALTHY)
    inst.state = ModelState.STARTING
    inst.managed = True
    inst.desired = Desired.RUNNING
    inst.proc = FakeProc()
    log = tmp_path / "run.log"
    log.write_text("stuck\n", encoding="utf-8")
    inst.log_path = str(log)
    inst.last_log_size = log.stat().st_size  # already observed -> no new progress
    inst.started_at = time.time() - 10_000
    inst.last_progress_at = time.time() - 10_000

    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings())
    assert inst.state == ModelState.FAILED
    assert inst.next_restart_at is not None  # recovery armed


async def test_starting_with_dead_process_is_failed():
    reg = _registry()
    inst = reg.get(UNHEALTHY)
    inst.state = ModelState.STARTING
    inst.managed = True
    inst.proc = FakeProc(returncode=1)  # already exited
    inst.started_at = time.time()

    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings())
    assert inst.state == ModelState.FAILED
    assert "process exited" in inst.last_error
    assert inst.proc is None


async def test_ready_with_dead_process_flips_to_failed():
    reg = _registry()
    inst = reg.get(HEALTHY)
    inst.state = ModelState.READY
    inst.managed = True
    inst.proc = FakeProc(returncode=139)

    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings())
    assert inst.state == ModelState.FAILED


async def test_stopping_with_dead_process_becomes_stopped():
    reg = _registry()
    inst = reg.get(HEALTHY)
    inst.state = ModelState.STOPPING
    inst.managed = True
    inst.proc = FakeProc(returncode=0)

    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings())
    assert inst.state == ModelState.STOPPED
    assert inst.proc is None


async def test_ready_health_miss_stays_ready_but_records_error():
    reg = _registry()
    inst = reg.get(HEALTHY)
    inst.state = ModelState.READY
    inst.managed = True
    inst.proc = FakeProc()  # alive

    await reconcile_once(reg, FakeHTTPClient(healthy_ports=set()), _settings())
    assert inst.state == ModelState.READY  # no flap
    assert inst.last_error is not None


async def test_adopt_running_marks_healthy_unmanaged():
    reg = _registry()
    await adopt_running(reg, FakeHTTPClient(healthy_ports={8002}), _settings())

    adopted = reg.get(HEALTHY)
    assert adopted.state == ModelState.READY
    assert adopted.managed is False
    assert adopted.desired == Desired.RUNNING

    assert reg.get(UNHEALTHY).state == ModelState.STOPPED


# -- alerting: the core fix — a reconciler-detected crash now fires an alert ----

class _CollectingNotifier:
    def __init__(self):
        self.events = []

    async def notify(self, ev):
        self.events.append(ev)


async def test_reconciler_crash_fires_alert():
    """The gap this closes: a crash detected by the reconciler (not the manager)
    must dispatch an alert, attributed model_failed, and arm a restart."""
    reg = _registry()
    inst = reg.get(HEALTHY)
    inst.state = ModelState.READY
    inst.managed = True
    inst.desired = Desired.RUNNING
    inst.proc = FakeProc(returncode=139)  # crashed

    notifier = _CollectingNotifier()
    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings(),
                         notifier=notifier)
    assert inst.state == ModelState.FAILED
    assert [e.event for e in notifier.events] == ["model_failed"]
    assert notifier.events[0].severity == "error"


async def test_reconciler_gave_up_when_restart_budget_exhausted():
    """Once the restart budget is spent and nothing is armed, the crash escalates
    to model_gave_up (critical)."""
    settings = BackendSettings(max_restarts=3)
    reg = _registry()
    inst = reg.get(HEALTHY)
    inst.state = ModelState.READY
    inst.managed = True
    inst.desired = Desired.RUNNING
    inst.restart_count = 3            # budget already spent
    inst.proc = FakeProc(returncode=1)

    notifier = _CollectingNotifier()
    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), settings,
                         notifier=notifier)
    assert inst.state == ModelState.FAILED
    assert inst.next_restart_at is None  # not re-armed
    assert [e.event for e in notifier.events] == ["model_gave_up"]
    assert notifier.events[0].severity == "critical"

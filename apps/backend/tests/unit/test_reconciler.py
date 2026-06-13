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


async def test_starting_times_out_to_failed():
    reg = _registry()
    inst = reg.get(UNHEALTHY)
    inst.state = ModelState.STARTING
    inst.managed = True
    inst.proc = FakeProc()
    inst.started_at = time.time() - 10_000  # well past start_timeout

    await reconcile_once(reg, FakeHTTPClient(healthy_ports={8002}), _settings())
    assert inst.state == ModelState.FAILED
    assert "timeout" in inst.last_error


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

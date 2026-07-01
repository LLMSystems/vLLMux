"""Per-node actuation (HA Phase 7): converge_desired drives owned instances toward
their persisted desired state, skipping foreign (other-node-owned) keys."""
import pytest

from app.core.settings import BackendSettings
from app.llmops.launchers import EmbeddingLauncher, VllmLauncher
from app.llmops.manager import build_registry
from app.llmops.reconciler import converge_desired
from app.llmops.state import Desired, ModelState
from tests.conftest import FAKE_CONFIG

pytestmark = pytest.mark.unit

A = "Qwen3-0.6B::qwen3"
B = "Qwen3-0.6B::qwen3-2"


def _registry():
    return build_registry(FAKE_CONFIG, "config.yaml", [VllmLauncher(), EmbeddingLauncher()])


class _DesiredStore:
    def __init__(self, desired: dict[str, str]):
        self._desired = desired

    async def list_instance_desired(self) -> dict[str, str]:
        return dict(self._desired)


class _RecordingManager:
    """Records the actuation calls converge_desired makes."""
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def start(self, key, **kw): self.calls.append(("start", key))
    async def stop(self, key, **kw): self.calls.append(("stop", key))
    async def sleep(self, key, **kw): self.calls.append(("sleep", key))
    async def wake(self, key, **kw): self.calls.append(("wake", key))


async def _run(states: dict[str, ModelState], desired: dict[str, str], foreign=frozenset()):
    reg = _registry()
    for key, st in states.items():
        reg.get(key).state = st
    mgr = _RecordingManager()
    await converge_desired(reg, BackendSettings(), _DesiredStore(desired), mgr, set(foreign))
    return mgr.calls


async def test_starts_stopped_but_wanted():
    calls = await _run({A: ModelState.STOPPED}, {A: Desired.RUNNING.value})
    assert calls == [("start", A)]


async def test_stops_live_but_unwanted():
    calls = await _run({A: ModelState.READY}, {A: Desired.STOPPED.value})
    assert calls == [("stop", A)]


async def test_sleeps_ready_when_asleep_wanted():
    calls = await _run({A: ModelState.READY}, {A: Desired.ASLEEP.value})
    assert calls == [("sleep", A)]


async def test_wakes_sleeping_when_running_wanted():
    calls = await _run({A: ModelState.SLEEPING}, {A: Desired.RUNNING.value})
    assert calls == [("wake", A)]


async def test_failed_is_left_to_restart_logic():
    # desired=running + FAILED must NOT be started here (that is _process_restarts'
    # job, with budget/backoff) — only STOPPED-but-wanted is converged.
    calls = await _run({A: ModelState.FAILED}, {A: Desired.RUNNING.value})
    assert calls == []


async def test_already_converged_is_noop():
    calls = await _run({A: ModelState.READY}, {A: Desired.RUNNING.value})
    assert calls == []


async def test_foreign_keys_skipped():
    # A owned by another live node -> not actuated here; B (ours) is.
    calls = await _run(
        {A: ModelState.STOPPED, B: ModelState.STOPPED},
        {A: Desired.RUNNING.value, B: Desired.RUNNING.value},
        foreign={A},
    )
    assert calls == [("start", B)]


async def test_unknown_key_skipped():
    # A desired entry with no registry instance is ignored (no crash).
    calls = await _run({}, {"Ghost::x": Desired.RUNNING.value})
    assert calls == []

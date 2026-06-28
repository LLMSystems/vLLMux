"""Desired-state persistence + boot replay (HA Phase 2b): start/stop/sleep/wake
record the user's intent, and replay_desired restores RUNNING models after a
restart / replica takeover."""
import pytest

from app.llmops import manager as manager_mod
from app.llmops.state import Desired, ModelState
from tests.conftest import FakeProc

pytestmark = pytest.mark.unit

KEY = "Qwen3-0.6B::qwen3"


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch):
    monkeypatch.setattr(manager_mod, "spawn_process", lambda spec: FakeProc())


async def test_start_and_stop_persist_desired(app):
    mgr = app.state.manager
    mgr.registry.get(KEY).set_state(ModelState.STOPPED)
    await mgr.start(KEY)
    assert app.state.store.desired[KEY] == "running"
    await mgr.stop(KEY)
    assert app.state.store.desired[KEY] == "stopped"


async def test_replay_starts_desired_running_when_stopped(app):
    mgr = app.state.manager
    await mgr.store.set_instance_desired(KEY, "running")
    inst = mgr.registry.get(KEY)
    inst.set_state(ModelState.STOPPED)

    await mgr.replay_desired()

    assert inst.state in (ModelState.STARTING, ModelState.READY)
    assert inst.desired == Desired.RUNNING


async def test_replay_skips_already_running_and_non_running_desired(app):
    mgr = app.state.manager
    # 'running' but already READY -> left alone (no double start).
    inst = mgr.registry.get(KEY)
    inst.set_state(ModelState.READY)
    inst.proc = FakeProc()
    await mgr.store.set_instance_desired(KEY, "running")
    # a stopped instance whose desired is 'stopped' -> not started.
    other = "Qwen3-0.6B::qwen3-2"
    mgr.registry.get(other).set_state(ModelState.STOPPED)
    await mgr.store.set_instance_desired(other, "stopped")

    await mgr.replay_desired()

    assert mgr.registry.get(KEY).state == ModelState.READY
    assert mgr.registry.get(other).state == ModelState.STOPPED


async def test_replay_skips_instance_assigned_to_another_node(app):
    # HA Phase 3b: an instance owned by a different node-agent must not be replayed
    # by this node — its node restores it.
    mgr = app.state.manager
    inst = mgr.registry.get(KEY)
    inst.set_state(ModelState.STOPPED)
    await mgr.store.set_instance_desired(KEY, "running")
    await mgr.store.set_assignment(KEY, "some-other-node")

    await mgr.replay_desired()

    assert inst.state == ModelState.STOPPED  # left for the owning node


async def test_foreign_assignments_excludes_local_and_unassigned(app):
    mgr = app.state.manager
    me = mgr.settings.instance_id
    await mgr.store.set_assignment("Qwen3-0.6B::qwen3", me)            # mine
    await mgr.store.set_assignment("Qwen3-0.6B::qwen3-2", "node-zzz")  # foreign
    foreign = await mgr.foreign_assignments()
    assert foreign == {"Qwen3-0.6B::qwen3-2"}


async def _arm_for_restart(mgr):
    import time as _t
    inst = mgr.registry.get(KEY)
    inst.state = ModelState.FAILED
    inst.managed = True
    inst.desired = Desired.RUNNING
    inst.restart_count = 0
    inst.next_restart_at = _t.time() - 1  # due now
    return inst


async def test_auto_restart_skips_foreign_assigned(app):
    from app.core.settings import BackendSettings
    from app.llmops.reconciler import _process_restarts
    mgr = app.state.manager
    inst = await _arm_for_restart(mgr)
    await mgr.store.set_assignment(KEY, "other-node")

    await _process_restarts(mgr.registry, BackendSettings(), mgr.store, mgr)
    assert inst.state == ModelState.FAILED  # owning node restarts it, not us


async def test_auto_restart_runs_when_owned(app):
    from app.core.settings import BackendSettings
    from app.llmops.reconciler import _process_restarts
    mgr = app.state.manager
    inst = await _arm_for_restart(mgr)  # unassigned -> owned by default (collapsed)

    await _process_restarts(mgr.registry, BackendSettings(), mgr.store, mgr)
    assert inst.state in (ModelState.STARTING, ModelState.READY)  # restarted


async def test_replay_noop_without_store():
    # A store-less manager (shouldn't happen in prod) must not blow up.
    from app.core.settings import BackendSettings
    from app.llmops.launchers import VllmLauncher
    from app.llmops.manager import ModelManager, build_registry
    from tests.conftest import FAKE_CONFIG

    reg = build_registry(FAKE_CONFIG, "config.yaml", [VllmLauncher()])
    mgr = ModelManager(reg, [VllmLauncher()], None, FAKE_CONFIG, "config.yaml",
                       BackendSettings(), store=None)
    await mgr.replay_desired()  # no exception

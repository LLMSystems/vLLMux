"""ModelManager.sleep()/wake(): level-1 warm-standby tier.

sleep() pages weights to CPU (frees VRAM) on a READY instance; wake() brings it
back. Both call vLLM dev endpoints, gate on state/capability, and revert the
desired intent if the HTTP call fails.
"""
import pytest

from app.core.settings import BackendSettings
from app.llmops.instance import LaunchSpec, ModelInstance
from app.llmops.manager import ModelConflict, ModelManager, ModelNotFound, SleepError
from app.llmops.registry import ModelRegistry
from app.llmops.state import Desired, ModelKind, ModelState
from schema import RootConfig

pytestmark = pytest.mark.unit


class _Resp:
    def __init__(self, code=200):
        self.status_code = code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeClient:
    """Records POSTs; returns 500 when `fail` is set (to exercise rollback)."""

    def __init__(self, fail=False):
        self.calls: list[str] = []
        self.fail = fail

    async def post(self, url, json=None, timeout=None):
        self.calls.append(url)
        return _Resp(500 if self.fail else 200)


def _config() -> RootConfig:
    return RootConfig.model_validate({
        "server": {"host": "0.0.0.0", "port": 8887},
        "LLM_engines": {
            "Qwen": {
                "instances": [{"id": "a", "host": "localhost", "port": 8002, "cuda_device": 0}],
                "model_config": {"model_tag": "Qwen/Qwen3-0.6B", "enable_sleep_mode": True},
            }
        },
    })


def _inst(state=ModelState.READY, sleep_enabled=True, port=8002) -> ModelInstance:
    spec = LaunchSpec(
        key="Qwen::a", kind=ModelKind.LLM, command=[], env={}, log_path="x.log",
        host="localhost", port=port, probe_url=f"http://localhost:{port}/health",
        sleep_enabled=sleep_enabled,
    )
    inst = ModelInstance(
        key="Qwen::a", kind=ModelKind.LLM, host="localhost", port=port,
        spec=spec, model_tag="Qwen/Qwen3-0.6B", managed=True, proc=object(),
    )
    inst.set_state(state)
    return inst


def _mgr(client, inst):
    reg = ModelRegistry()
    reg.add(inst)
    return ModelManager(reg, [], client, _config(), "config.yaml",
                        BackendSettings(), overlay_path="overlay.json")


async def test_sleep_ready_instance_sets_sleeping_and_calls_endpoint():
    client = FakeClient()
    inst = _inst(ModelState.READY)
    mgr = _mgr(client, inst)

    res = await mgr.sleep("Qwen::a")

    assert res.state == ModelState.SLEEPING
    assert res.desired == Desired.ASLEEP
    assert client.calls == ["http://localhost:8002/sleep?level=1"]


async def test_sleep_rejected_when_not_sleep_enabled():
    inst = _inst(ModelState.READY, sleep_enabled=False)
    mgr = _mgr(FakeClient(), inst)
    with pytest.raises(ModelConflict):
        await mgr.sleep("Qwen::a")
    assert inst.state == ModelState.READY  # unchanged


async def test_sleep_rejected_when_not_ready():
    inst = _inst(ModelState.STOPPED)
    mgr = _mgr(FakeClient(), inst)
    with pytest.raises(ModelConflict):
        await mgr.sleep("Qwen::a")


async def test_sleep_http_failure_reverts_desired_and_keeps_ready():
    client = FakeClient(fail=True)
    inst = _inst(ModelState.READY)
    mgr = _mgr(client, inst)
    with pytest.raises(SleepError):
        await mgr.sleep("Qwen::a")
    assert inst.state == ModelState.READY
    assert inst.desired == Desired.RUNNING  # intent reverted, not left ASLEEP


async def test_wake_sleeping_instance_returns_to_ready():
    client = FakeClient()
    inst = _inst(ModelState.SLEEPING)
    inst.desired = Desired.ASLEEP
    mgr = _mgr(client, inst)

    res = await mgr.wake("Qwen::a")

    assert res.state == ModelState.READY
    assert res.desired == Desired.RUNNING
    assert client.calls == ["http://localhost:8002/wake_up"]


async def test_wake_rejected_when_not_sleeping():
    inst = _inst(ModelState.READY)
    mgr = _mgr(FakeClient(), inst)
    with pytest.raises(ModelConflict):
        await mgr.wake("Qwen::a")


async def test_wake_http_failure_reverts_to_sleeping():
    client = FakeClient(fail=True)
    inst = _inst(ModelState.SLEEPING)
    inst.desired = Desired.ASLEEP
    mgr = _mgr(client, inst)
    with pytest.raises(SleepError):
        await mgr.wake("Qwen::a")
    assert inst.state == ModelState.SLEEPING
    assert inst.desired == Desired.ASLEEP


async def test_sleep_unknown_model():
    mgr = _mgr(FakeClient(), _inst())
    with pytest.raises(ModelNotFound):
        await mgr.sleep("Ghost::x")

import os
import sys

import pytest
from fastapi.testclient import TestClient

# Make the backend package root importable (main, app.*) regardless of cwd.
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import main  # noqa: E402  (importing app.core.config puts config-schema on sys.path)
from app.core.settings import BackendSettings  # noqa: E402
from app.llmops.launchers import EmbeddingLauncher, VllmLauncher  # noqa: E402
from app.llmops.manager import ModelManager, build_registry  # noqa: E402
from schema import RootConfig  # noqa: E402

FAKE_CONFIG_DICT = {
    "server": {"host": "0.0.0.0", "port": 8887},
    "LLM_engines": {
        "Qwen3-0.6B": {
            "instances": [
                {"id": "qwen3", "host": "localhost", "port": 8002, "cuda_device": 0},
                {"id": "qwen3-2", "host": "localhost", "port": 8004, "cuda_device": 0},
            ],
            "model_config": {
                "model_tag": "Qwen/Qwen3-0.6B",
                "max_model_len": 500,
                "gpu_memory_utilization": 0.35,
            },
        }
    },
    "embedding_server": {
        "host": "localhost",
        "port": 8005,
        "cuda_device": 1,
        "embedding_models": {"m3e-base": {"model_name": "m3e"}, "bge-m3": {"model_name": "bge-m3"}},
        "reranking_models": {"bge-reranker-large": {"model_name": "bge-rr"}},
    },
}

FAKE_CONFIG = RootConfig.model_validate(FAKE_CONFIG_DICT)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeHTTPClient:
    """Stand-in for app.state.http_client; only `healthy_ports` answer 200."""

    def __init__(self, healthy_ports=()):
        self.healthy_ports = set(healthy_ports)

    async def get(self, url, *args, **kwargs):
        # url looks like http://localhost:8002/health
        port = int(url.split(":")[2].split("/")[0])
        if port in self.healthy_ports:
            return _FakeResponse(200)
        raise ConnectionError("refused")


class FakeProc:
    """A subprocess.Popen stand-in with controllable liveness."""

    def __init__(self, pid: int = 12345, returncode=None):
        self.pid = pid
        self._returncode = returncode

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        return self._returncode

    def exit(self, returncode: int = 0):
        self._returncode = returncode


class FakeStore:
    """In-memory stand-in for LLMOpsStore; records writes, serves simple reads.

    Real aggregation/SQL is covered by packages/llmops-store tests; here we only
    need the route/manager wiring to call the right methods with the right args.
    """

    def __init__(self):
        self.events = []  # (key, kind, from_state, to_state, detail)
        self.reqs = []    # kwargs dicts

    async def record_model_event(self, key, kind, from_state, to_state, detail=None, ts=None):
        self.events.append((key, kind, from_state, to_state, detail))

    async def record_request(self, **kwargs):
        self.reqs.append(kwargs)

    async def recent_events(self, key=None, limit=100):
        rows = [
            {"key": k, "kind": kd, "from_state": f, "to_state": t, "detail": d}
            for (k, kd, f, t, d) in self.events
            if key is None or k == key
        ]
        return list(reversed(rows))[:limit]

    async def recent_requests(self, model_key=None, limit=100):
        rows = [r for r in self.reqs if model_key is None or r.get("model_key") == model_key]
        return list(reversed(rows))[:limit]

    async def usage_summary(self, since=None):
        agg: dict = {}
        for r in self.reqs:
            agg[r.get("model_key")] = agg.get(r.get("model_key"), 0) + 1
        return [{"model_key": k, "count": c} for k, c in agg.items()]


@pytest.fixture
def fake_config():
    return FAKE_CONFIG


@pytest.fixture
def app(monkeypatch):
    """The real FastAPI app with llmops state populated manually.

    We do NOT enter TestClient as a context manager, so the real lifespan never
    runs — the state below is the test's single source of truth. Process spawning
    is patched out so start/stop never touch the OS.
    """
    from app.llmops import manager as manager_mod

    monkeypatch.setattr(manager_mod, "spawn_process", lambda spec: FakeProc())
    monkeypatch.setattr(manager_mod, "terminate_process_group", lambda proc, timeout=10.0: None)

    launchers = [VllmLauncher(), EmbeddingLauncher()]
    registry = build_registry(FAKE_CONFIG, "config.yaml", launchers)
    http_client = FakeHTTPClient(healthy_ports={8002})
    settings = BackendSettings()
    store = FakeStore()

    application = main.app
    application.state.config = FAKE_CONFIG
    application.state.config_path = "config.yaml"
    application.state.settings = settings
    application.state.http_client = http_client
    application.state.store = store
    application.state.registry = registry
    application.state.manager = ModelManager(
        registry, launchers, http_client, FAKE_CONFIG, "config.yaml", settings, store=store
    )
    application.state.gpu_processes = []
    return application


@pytest.fixture
def client(app):
    return TestClient(app)

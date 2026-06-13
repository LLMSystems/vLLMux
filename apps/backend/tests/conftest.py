import os
import sys

import pytest
from fastapi.testclient import TestClient

# Make the backend package root importable (main, app.*) regardless of cwd.
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import main  # noqa: E402

FAKE_CONFIG = {
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
        "embedding_models": {"m3e-base": {}, "bge-m3": {}},
        "reranking_models": {"bge-reranker-large": {}},
    },
}


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeHTTPClient:
    """Stand-in for app.state.http_client; only port `healthy_ports` answer 200."""

    def __init__(self, healthy_ports=()):
        self.healthy_ports = set(healthy_ports)

    async def get(self, url, *args, **kwargs):
        # url looks like http://localhost:8002/health
        port = int(url.split(":")[2].split("/")[0])
        if port in self.healthy_ports:
            return _FakeResponse(200)
        raise ConnectionError("refused")


@pytest.fixture
def app():
    """The real FastAPI app with state populated manually.

    We deliberately do NOT enter TestClient as a context manager, so the real
    lifespan (which spawns the nvidia-smi polling task and reads config from
    disk) never runs — state below is the test's single source of truth.
    """
    application = main.app
    application.state.config = FAKE_CONFIG
    application.state.config_path = "config.yaml"
    application.state.starting_models = set()
    application.state.http_client = FakeHTTPClient(healthy_ports={8002})
    application.state.gpu_processes = []
    application.state.running_llm_procs = {}
    return application


@pytest.fixture
def client(app):
    return TestClient(app)

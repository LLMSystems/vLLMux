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
from app.perf.manager import PerfManager  # noqa: E402
from app.services.downloads import DownloadManager  # noqa: E402
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
        self.posts = []  # (url, json) — e.g. alert sends

    async def get(self, url, *args, **kwargs):
        # url looks like http://localhost:8002/health
        port = int(url.split(":")[2].split("/")[0])
        if port in self.healthy_ports:
            return _FakeResponse(200)
        raise ConnectionError("refused")

    async def post(self, url, json=None, timeout=None, *args, **kwargs):
        self.posts.append((url, json))
        return _FakeResponse(200)


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
        self.api_keys = []  # list of dicts (incl. key_hash)
        self.operators = []  # list of dicts (incl. token_hash)
        self.audit = []  # list of dicts
        self.alert_sinks = []  # list of dicts

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

    # -- API keys --
    async def create_api_key(self, name, key_hash, prefix, rpm_limit=None,
                             token_quota=None, quota_period=None, ts=None):
        kid = len(self.api_keys) + 1
        self.api_keys.append({
            "id": kid, "name": name, "key_hash": key_hash, "prefix": prefix,
            "created_at": ts or 0.0, "last_used_at": None, "revoked": 0,
            "rpm_limit": rpm_limit, "token_quota": token_quota, "quota_period": quota_period,
        })
        return kid

    async def tokens_used_by_key(self, name, since=None):
        return sum(
            r.get("total_tokens") or 0
            for r in self.reqs
            if r.get("api_key_name") == name and (since is None or r.get("ts", 0) >= since)
        )

    async def list_api_keys(self):
        return [{k: v for k, v in r.items() if k != "key_hash"} for r in reversed(self.api_keys)]

    # -- Operators --
    async def create_operator(self, label, token_hash, prefix, role, ts=None):
        oid = len(self.operators) + 1
        self.operators.append({
            "id": oid, "label": label, "token_hash": token_hash, "prefix": prefix,
            "role": role, "created_at": ts or 0.0, "last_used_at": None, "revoked": 0,
        })
        return oid

    async def list_operators(self):
        return [{k: v for k, v in r.items() if k != "token_hash"}
                for r in reversed(self.operators)]

    async def count_active_operators(self):
        return sum(1 for o in self.operators if not o["revoked"])

    async def get_active_operator_by_hash(self, token_hash):
        for o in self.operators:
            if o["token_hash"] == token_hash and not o["revoked"]:
                return dict(o)
        return None

    async def touch_operator(self, operator_id, ts=None):
        for o in self.operators:
            if o["id"] == operator_id:
                o["last_used_at"] = ts or 0.0

    async def revoke_operator(self, operator_id):
        for o in self.operators:
            if o["id"] == operator_id and not o["revoked"]:
                o["revoked"] = 1
                return True
        return False

    async def set_operator_role(self, operator_id, role):
        for o in self.operators:
            if o["id"] == operator_id and not o["revoked"]:
                o["role"] = role
                return True
        return False

    async def rotate_operator_token(self, operator_id, token_hash, prefix):
        for o in self.operators:
            if o["id"] == operator_id and not o["revoked"]:
                o["token_hash"] = token_hash
                o["prefix"] = prefix
                o["last_used_at"] = None
                return True
        return False

    # -- Audit --
    async def record_audit(self, actor, method, path, status, role=None, target=None,
                           detail=None, source_ip=None, ts=None):
        self.audit.append({
            "id": len(self.audit) + 1, "ts": ts or 0.0, "actor": actor, "role": role,
            "method": method, "path": path, "target": target, "status": status,
            "detail": detail, "source_ip": source_ip,
        })

    # -- Alert sinks --
    async def create_alert_sink(self, type, url, min_severity="info", ts=None):
        sid = len(self.alert_sinks) + 1
        self.alert_sinks.append({
            "id": sid, "type": type, "url": url, "min_severity": min_severity,
            "created_at": ts or 0.0,
        })
        return sid

    async def list_alert_sinks(self):
        return [dict(s) for s in self.alert_sinks]

    async def get_alert_sink(self, sink_id):
        return next((dict(s) for s in self.alert_sinks if s["id"] == sink_id), None)

    async def delete_alert_sink(self, sink_id):
        before = len(self.alert_sinks)
        self.alert_sinks = [s for s in self.alert_sinks if s["id"] != sink_id]
        return len(self.alert_sinks) < before

    async def list_audit(self, actor=None, action=None, target=None, since=None,
                         until=None, before=None, limit=200):
        rows = [
            r for r in self.audit
            if (actor is None or r["actor"] == actor)
            and (action is None or action in r["path"])
            and (target is None or r["target"] == target)
            and (since is None or r["ts"] >= since)
            and (until is None or r["ts"] <= until)
            and (before is None or r["id"] < before)
        ]
        return list(reversed(rows))[:limit]

    # -- Perf runs --
    def __init_perf(self):
        if not hasattr(self, "perf"):
            self.perf = []

    async def create_perf_run(self, model, target_url, params, name=None, ts=None):
        self.__init_perf()
        rid = len(self.perf) + 1
        self.perf.append({"id": rid, "model": model, "target_url": target_url, "params": params,
                          "name": name, "status": "running", "result": None, "output_dir": None,
                          "error": None, "created_at": 0.0, "started_at": 0.0, "finished_at": None})
        return rid

    async def finish_perf_run(self, run_id, status, result=None, output_dir=None, error=None, ts=None):
        self.__init_perf()
        for r in self.perf:
            if r["id"] == run_id:
                r.update(status=status, result=result, output_dir=output_dir, error=error)

    async def list_perf_runs(self, limit=50):
        self.__init_perf()
        return list(reversed(self.perf))[:limit]

    async def get_perf_run(self, run_id):
        self.__init_perf()
        return next((r for r in self.perf if r["id"] == run_id), None)

    async def delete_perf_run(self, run_id):
        self.__init_perf()
        before = len(self.perf)
        self.perf = [r for r in self.perf if r["id"] != run_id]
        return len(self.perf) < before

    async def mark_stale_perf_runs(self):
        self.__init_perf()

    # -- Eval runs --
    def __init_eval(self):
        if not hasattr(self, "evals"):
            self.evals = []

    async def create_eval_run(self, model, target_url, datasets, params, name=None, ts=None,
                              status="running"):
        self.__init_eval()
        rid = len(self.evals) + 1
        self.evals.append({"id": rid, "model": model, "target_url": target_url, "datasets": datasets,
                          "params": params, "name": name, "status": status, "result": None,
                          "output_dir": None, "error": None, "created_at": 0.0,
                          "started_at": None if status == "queued" else 0.0,
                          "finished_at": None})
        return rid

    async def start_eval_run(self, run_id, ts=None):
        self.__init_eval()
        for r in self.evals:
            if r["id"] == run_id:
                r.update(status="running", started_at=0.0)

    async def finish_eval_run(self, run_id, status, result=None, output_dir=None, error=None, ts=None):
        self.__init_eval()
        for r in self.evals:
            if r["id"] == run_id:
                r.update(status=status, result=result, output_dir=output_dir, error=error)

    async def list_eval_runs(self, limit=50):
        self.__init_eval()
        return list(reversed(self.evals))[:limit]

    async def get_eval_run(self, run_id):
        self.__init_eval()
        return next((r for r in self.evals if r["id"] == run_id), None)

    async def delete_eval_run(self, run_id):
        self.__init_eval()
        before = len(self.evals)
        self.evals = [r for r in self.evals if r["id"] != run_id]
        return len(self.evals) < before

    async def mark_stale_eval_runs(self):
        self.__init_eval()

    async def api_key_usage(self):
        agg: dict = {}
        for r in self.reqs:
            name = r.get("api_key_name")
            if not name:
                continue
            a = agg.setdefault(name, {"name": name, "request_count": 0, "total_tokens": 0, "last_ts": 0})
            a["request_count"] += 1
            a["total_tokens"] += r.get("total_tokens") or 0
        return agg

    async def get_active_api_key_by_hash(self, key_hash):
        for r in self.api_keys:
            if r["key_hash"] == key_hash and not r["revoked"]:
                return {"id": r["id"], "name": r["name"], "prefix": r["prefix"], "revoked": 0}
        return None

    async def revoke_api_key(self, key_id):
        for r in self.api_keys:
            if r["id"] == key_id and not r["revoked"]:
                r["revoked"] = 1
                return True
        return False


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
    # No real GPU in tests/CI: make the start preflights a no-op so start/stop
    # tests don't depend on the host's actual free VRAM. (Tests that exercise the
    # GPU guard override this locally.)
    from app.services import gpu_service

    monkeypatch.setattr(gpu_service, "get_gpu_info", lambda: [])

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
    from app.llmops.notifier import build_notifier
    application.state.notifier = build_notifier(http_client, settings)
    application.state.registry = registry
    application.state.manager = ModelManager(
        registry, launchers, http_client, FAKE_CONFIG, "config.yaml", settings, store=store,
        notifier=application.state.notifier,
    )
    application.state.gpu_processes = []
    application.state.download_manager = DownloadManager()
    from app.services.dataset_downloads import DatasetDownloadManager
    application.state.dataset_download_manager = DatasetDownloadManager()
    from app.services.lora_downloads import LoraDownloadManager
    application.state.lora_download_manager = LoraDownloadManager()
    application.state.perf_manager = PerfManager(
        store, application.state.manager, settings, str(BACKEND_ROOT), "http://127.0.0.1:8887"
    )
    from app.eval.manager import EvalManager
    application.state.eval_manager = EvalManager(
        store, application.state.manager, settings, str(BACKEND_ROOT), "http://127.0.0.1:8887"
    )
    # Mirror main.lifespan's cross-injection so the eval/load-test mutex works.
    application.state.eval_manager.perf_manager = application.state.perf_manager
    application.state.perf_manager.eval_manager = application.state.eval_manager
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_client(app):
    """Client with admin auth turned on (token = 'secret-admin')."""
    app.state.settings = BackendSettings(admin_token="secret-admin")
    return TestClient(app)

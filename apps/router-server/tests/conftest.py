import os
import sys
from types import SimpleNamespace

import pytest

# Router code imports as `src.llm_router.*`; make the repo root importable
# (src/ is used as a namespace package, mirroring `PYTHONPATH=.` at runtime).
ROUTER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROUTER_ROOT not in sys.path:
    sys.path.insert(0, ROUTER_ROOT)


@pytest.fixture
def make_app():
    """Factory for a minimal stand-in of the FastAPI app's `.state`.

    backend_selector only touches metrics_cache / backend_inflight /
    backend_health, so we don't need a real FastAPI instance.
    """

    def _make(
        metrics_cache=None,
        backend_inflight=None,
        backend_health=None,
        rr_counters=None,
        routing_strategy="least_load",
    ):
        return SimpleNamespace(
            state=SimpleNamespace(
                metrics_cache=metrics_cache or {},
                backend_inflight=backend_inflight or {},
                backend_health=backend_health or {},
                rr_counters=rr_counters if rr_counters is not None else {},
                routing_strategy=routing_strategy,
            )
        )

    return _make

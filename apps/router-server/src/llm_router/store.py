"""sys.path bootstrap + re-export for the shared llmops-store package.

Mirrors config_loader.py's bootstrap of config-schema so the router can write
request logs into the same SQLite file the dashboard backend reads.
"""
import os
import sys

# apps/router-server/src/llm_router/store.py -> repo root is 4 levels up.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")
)
_STORE_DIR = os.path.join(_REPO_ROOT, "packages", "llmops-store")
if _STORE_DIR not in sys.path:
    sys.path.insert(0, _STORE_DIR)

from llmops_store import LLMOpsStore  # noqa: E402,F401

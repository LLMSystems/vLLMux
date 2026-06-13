"""sys.path bootstrap + re-export for the shared llmops-store package.

Like core/config.py does for config-schema, this adds the standalone
packages/llmops-store directory to sys.path once so the backend can
`from app.core.store import LLMOpsStore`.
"""
import os
import sys

# apps/backend/app/core/store.py -> repo root is 4 levels up.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")
)
_STORE_DIR = os.path.join(_REPO_ROOT, "packages", "llmops-store")
if _STORE_DIR not in sys.path:
    sys.path.insert(0, _STORE_DIR)

from llmops_store import LLMOpsStore, get_db_path  # noqa: E402,F401

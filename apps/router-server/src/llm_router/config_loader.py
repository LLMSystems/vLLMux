"""Config loading for the router.

The canonical config + schema live in packages/config-schema. We validate against
the shared RootConfig at load time (fail fast on a malformed config) and return a
plain dict, so the rest of the router keeps its lightweight dict-based access and
nothing downstream needs to change.
"""
import os
import sys

# apps/router-server/src/llm_router/config_loader.py -> repo root is 4 levels up.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")
)
_CONFIG_SCHEMA_DIR = os.path.join(_REPO_ROOT, "packages", "config-schema")
if _CONFIG_SCHEMA_DIR not in sys.path:
    sys.path.insert(0, _CONFIG_SCHEMA_DIR)

from schema import load_config as _load_typed  # noqa: E402


def load_config(path: str) -> dict:
    """Load + validate the YAML config, returning it as a plain dict.

    The returned dict uses the original YAML keys (e.g. the per-engine
    `model_config` alias), so existing dict access in the router is unaffected.
    """
    data = _load_typed(path).model_dump(by_alias=True)
    # Preserve the historical "absent" semantics: callers do
    # config.get("embedding_server", {}), which only works if the key is missing
    # rather than present-but-None.
    if data.get("embedding_server") is None:
        data.pop("embedding_server", None)
    return data

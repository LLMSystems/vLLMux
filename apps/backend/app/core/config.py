"""Locate, load, and validate the dashboard config via the shared schema.

The canonical config lives in packages/config-schema/config.yaml and is defined
by packages/config-schema/schema.py (RootConfig). Previously the backend loaded
that file as a raw dict and never validated it; now it imports the shared schema
so a malformed config fails fast at startup with a clear pydantic error — which
is what the README already promised.

`schema.py` is a standalone module (no installable package), so we add its
directory to sys.path once, mirroring how tests/conftest.py bootstraps imports.
"""
import os
import sys
from typing import Optional

CONFIG_PATH_ENV = "LLM_ROUTER_SERVER_CONFIG_PATH"

# apps/backend/app/core/config.py -> repo root is 4 levels up.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")
)
_CONFIG_SCHEMA_DIR = os.path.join(_REPO_ROOT, "packages", "config-schema")
DEFAULT_CONFIG_PATH = os.path.join(_CONFIG_SCHEMA_DIR, "config.yaml")

if _CONFIG_SCHEMA_DIR not in sys.path:
    sys.path.insert(0, _CONFIG_SCHEMA_DIR)

from schema import RootConfig  # noqa: E402
from schema import load_config as _load_typed  # noqa: E402


def get_config_path() -> str:
    """Resolve the active config path: env override, else the shared package."""
    return os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH)


def load_config(config_path: Optional[str] = None) -> RootConfig:
    """Load + validate the YAML config, returning a typed RootConfig."""
    return _load_typed(config_path or get_config_path())

"""Single source of truth for locating and reading the dashboard config.yaml.

Previously the config path / yaml load was re-implemented in three places
(main.py, routes/config.py, launcher/embedding_launcher.py). Everything now goes
through here so there is exactly one rule for *where* the config lives and *how*
it is parsed.
"""
import os
from typing import Any, Optional

import yaml

DEFAULT_CONFIG_PATH = "config.yaml"
CONFIG_PATH_ENV = "LLM_ROUTER_SERVER_CONFIG_PATH"


def get_config_path() -> str:
    """Resolve the active config path from the environment (single rule)."""
    return os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH)


def load_config(config_path: Optional[str] = None) -> dict[str, Any]:
    """Load and parse the YAML config. Defaults to :func:`get_config_path`."""
    path = config_path or get_config_path()
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

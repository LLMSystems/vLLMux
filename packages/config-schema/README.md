# config-schema

Single source of truth for the deployment configuration shared by the Dashboard
backend (`apps/backend`) and the LLM-Router-Server (`apps/router-server`).

## Files

- **`config.yaml`** — the one config both services read. Previously this was
  duplicated as `backend/config.yaml` and `router-server/configs/config.yaml`
  with no enforcement that they matched.
- **`schema.py`** — pydantic models + `load_config()` so both services validate
  the file the same way.

## How the services point at it

Both services resolve the config via the `LLM_ROUTER_SERVER_CONFIG_PATH`
environment variable.

- **Backend**: defaults to this file (see `apps/backend/app/core/config.py`); set
  `LLM_ROUTER_SERVER_CONFIG_PATH` to override.
- **Router**: pass the path into `scripts/start_all.sh`, e.g.
  `sh scripts/start_all.sh ../../packages/config-schema/config.yaml ./configs/gunicorn.conf.py`.

In Docker, `deploy/docker-compose.yaml` sets
`LLM_ROUTER_SERVER_CONFIG_PATH=/app/packages/config-schema/config.yaml`.

## Validation

```python
import sys; sys.path.insert(0, "packages/config-schema")
from schema import load_config
cfg = load_config()          # raises pydantic.ValidationError on bad config
print(cfg.server.port, list(cfg.LLM_engines))
```

The YAML key `model_config` maps to `.settings` on `LLMEngine` (the name
`model_config` is reserved by pydantic v2).

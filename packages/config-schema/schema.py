"""Shared pydantic schema + loader for the single config.yaml.

This package is the single source of truth for the deployment config that both
the Dashboard backend and the LLM-Router-Server consume. `config.yaml` (next to
this file) is the canonical config; the models below give both services the same
validation.

Usage:
    from schema import load_config, DEFAULT_CONFIG_PATH
    cfg = load_config()                 # -> RootConfig (validated)
    cfg = load_config("/path/to.yaml")  # explicit path

Standalone (no install required): add this directory to sys.path and import.
"""
import os
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    host: str = "0.0.0.0"
    port: int
    uvicorn_log_level: str = "info"


class InstanceConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    host: str = "localhost"
    port: int
    cuda_device: Optional[int] = None


class LoraModule(BaseModel):
    """One LoRA adapter statically mounted on a base model at `vllm serve` time.

    Mirrors vLLM's `--lora-modules` JSON form. `name` is the served name clients
    put in the request `model` field; `path` is a local dir or HF repo id;
    `base_model_name` (optional) enriches the `/v1/models` lineage."""
    model_config = ConfigDict(extra="allow", protected_namespaces=())
    name: str
    path: str
    base_model_name: Optional[str] = None


class EngineModelConfig(BaseModel):
    # extra="allow" so any vLLM flag can be passed through; protected_namespaces
    # silences the spurious "model_" field warnings.
    model_config = ConfigDict(extra="allow", protected_namespaces=())
    model_tag: str
    dtype: Optional[str] = None
    max_model_len: Optional[int] = None
    gpu_memory_utilization: Optional[float] = None
    tensor_parallel_size: int = 1
    # LoRA: `enable_lora`/`max_lora_rank`/… flow through extra="allow" as plain
    # vLLM flags; `lora_modules` is typed so the dashboard can render + manage the
    # adapters and the launcher can emit the multi-arg `--lora-modules` form.
    lora_modules: Optional[list[LoraModule]] = None
    # Cross-instance KV-cache sharing. A vLLM `--kv-transfer-config` payload
    # (e.g. OffloadingConnector over a shared root_dir); typed so it's discoverable
    # and the launcher emits it as one JSON arg + sets PYTHONHASHSEED=0. Free-form
    # dict because the connector schema varies. See docs/vllm-kv-cache-cross-instance-design.md.
    kv_transfer_config: Optional[dict] = None


class LLMEngine(BaseModel):
    # populate_by_name lets us refer to `.settings` in code while the YAML key
    # stays the historical `model_config`.
    model_config = ConfigDict(extra="allow", populate_by_name=True, protected_namespaces=())
    instances: list[InstanceConfig] = Field(default_factory=list)
    settings: EngineModelConfig = Field(alias="model_config")


class EmbeddingModelEntry(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())
    model_name: str
    model_path: Optional[str] = None
    tokenizer_path: Optional[str] = None
    max_length: int = 512
    use_gpu: bool = True
    use_float16: bool = True


class EmbeddingServer(BaseModel):
    model_config = ConfigDict(extra="allow")
    host: str = "localhost"
    port: int
    cuda_device: Optional[int] = None
    embedding_models: dict[str, EmbeddingModelEntry] = Field(default_factory=dict)
    reranking_models: dict[str, EmbeddingModelEntry] = Field(default_factory=dict)


class RootConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    server: ServerConfig
    LLM_engines: dict[str, LLMEngine] = Field(default_factory=dict)
    embedding_server: Optional[EmbeddingServer] = None


def load_raw(path: Optional[str] = None) -> dict:
    """Load the YAML config as a plain dict (no validation)."""
    with open(path or DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(path: Optional[str] = None) -> RootConfig:
    """Load + validate the config, returning a typed RootConfig."""
    return RootConfig.model_validate(load_raw(path))

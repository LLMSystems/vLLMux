import textwrap

import pytest

from src.llm_router.config_loader import get_model_route_table, load_config

pytestmark = pytest.mark.unit


def _write(tmp_path, body):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def test_load_config_reads_yaml(tmp_path):
    path = _write(
        tmp_path,
        """
        server:
          host: 0.0.0.0
          port: 8887
        """,
    )
    cfg = load_config(path)
    assert cfg["server"]["port"] == 8887


def test_route_table_maps_group_with_port_to_v1_url(tmp_path):
    path = _write(
        tmp_path,
        """
        LLM_engines:
          GroupWithPort:
            port: 8002
          GroupWithoutPort:
            instances:
              - id: a
                port: 8004
        """,
    )
    table = get_model_route_table(path)
    assert table == {"GroupWithPort": "http://localhost:8002/v1"}

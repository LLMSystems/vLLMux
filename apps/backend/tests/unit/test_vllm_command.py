import pytest

from app.services.vllm_command import parse_vllm_command

pytestmark = pytest.mark.unit


def test_parse_basic_serve_command():
    p = parse_vllm_command("vllm serve org/m --port 8001 --dtype float16 --enable-lora")
    assert p["model_config"]["model_tag"] == "org/m"
    assert p["instance"]["port"] == 8001
    assert p["model_config"]["dtype"] == "float16"
    assert p["model_config"]["enable_lora"] is True


def test_parse_lora_modules_short_form():
    p = parse_vllm_command(
        "vllm serve org/m --enable-lora --lora-modules sql=repo/sql fin=/models/fin --port 8000"
    )
    mods = p["model_config"]["lora_modules"]
    assert mods == [
        {"name": "sql", "path": "repo/sql"},
        {"name": "fin", "path": "/models/fin"},
    ]
    # the trailing --port is still parsed as an instance flag, not swallowed.
    assert p["instance"]["port"] == 8000


def test_parse_lora_modules_json_form():
    cmd = (
        "vllm serve org/m --enable-lora "
        '--lora-modules \'{"name":"sql","path":"repo/sql","base_model_name":"org/m"}\''
    )
    mods = parse_vllm_command(cmd)["model_config"]["lora_modules"]
    assert mods == [{"name": "sql", "path": "repo/sql", "base_model_name": "org/m"}]

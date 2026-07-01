import pytest

from app.services.vllm_command import parse_command, parse_sglang_command, parse_vllm_command

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


def test_parse_sglang_command_maps_flags_and_sets_engine():
    p = parse_sglang_command(
        "CUDA_VISIBLE_DEVICES=1 python -m sglang.launch_server --model-path org/m "
        "--port 8030 --context-length 4096 --mem-fraction-static 0.85 --tp-size 2"
    )
    mc = p["model_config"]
    assert mc["model_tag"] == "org/m"
    assert mc["engine"] == "sglang"
    # SGLang's renamed flags map back to engine-neutral keys.
    assert mc["max_model_len"] == 4096
    assert mc["gpu_memory_utilization"] == 0.85
    assert mc["tensor_parallel_size"] == 2
    assert p["instance"]["port"] == 8030
    assert p["instance"]["cuda_device"] == 1
    # No SGLang-renamed keys leak through un-mapped.
    assert "context_length" not in mc and "mem_fraction_static" not in mc


def test_parse_sglang_lora_paths():
    p = parse_sglang_command(
        "python -m sglang.launch_server --model-path org/m "
        "--lora-paths sql=repo/sql fin=/models/fin --port 8031"
    )
    mc = p["model_config"]
    assert mc["enable_lora"] is True
    assert mc["lora_modules"] == [
        {"name": "sql", "path": "repo/sql"},
        {"name": "fin", "path": "/models/fin"},
    ]
    assert p["instance"]["port"] == 8031


def test_parse_command_dispatch_by_sniffing_and_hint():
    # Sniffed from the command text.
    assert parse_command("python -m sglang.launch_server --model-path org/m")["model_config"]["engine"] == "sglang"
    # vLLM path leaves engine unset (frontend defaults to vllm).
    assert "engine" not in parse_command("vllm serve org/m --port 8001")["model_config"]
    # Explicit hint wins even for an ambiguous command.
    assert parse_command("--model-path org/m --port 8030", engine="sglang")["model_config"]["engine"] == "sglang"

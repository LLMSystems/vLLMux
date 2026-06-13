import argparse
import json
import subprocess

import uvloop
from vllm.entrypoints.openai.api_server import run_server
from vllm.entrypoints.openai.cli_args import (make_arg_parser,
                                              validate_parsed_serve_args)
from vllm.logger import init_logger
from vllm.utils.argparse_utils import FlexibleArgumentParser

logger = init_logger(__name__)

def build_args_from_dict(model_cfg: dict) -> argparse.Namespace:
    model_tag = model_cfg.get("model_tag")
    if not model_tag:
        raise ValueError("model_cfg 必須包含 'model_tag'")
    
    cli_args = ["serve", model_tag]
    for key, value in model_cfg.items():
        if key == "model_tag" or value is None:
            continue
        cli_key = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                cli_args.append(cli_key)
        else:
            cli_args.append(cli_key)
            cli_args.append(str(value))
    
    parser = FlexibleArgumentParser()
    subparsers = parser.add_subparsers(required=True, dest="subparser")
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("model_tag")
    serve_parser = make_arg_parser(serve_parser)
    parsed_args = parser.parse_args(cli_args)
    validate_parsed_serve_args(parsed_args)
    return parsed_args

def start_vllm_server(args: argparse.Namespace):
    args.model = args.model_tag
    uvloop.run(run_server(args))

def build_cli_args_from_dict(model_cfg: dict) -> list:
    model_tag = model_cfg.get("model_tag")
    if not model_tag:
        raise ValueError("必須提供 model_tag")
    
    cli_args = ["serve", model_tag]

    for key, value in model_cfg.items():
        if key == "model_tag" or value is None:
            continue
        key_flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                cli_args.append(key_flag)
        elif isinstance(value, list):
            cli_args.append(key_flag)
            cli_args.append(json.dumps(value))
        else:
            cli_args.append(key_flag)
            cli_args.append(str(value))

    return cli_args

def start_vllm_subprocess(model_cfg):
    cli_args = build_cli_args_from_dict(model_cfg)
    logger.info(f"執行指令: vllm {' '.join(cli_args)}")
    subprocess.Popen(
        ["vllm"] + cli_args
    )
    

import argparse
import copy
import os
import signal
import subprocess
import sys
import time
from typing import Dict

from vllm.logger import init_logger

from src.llm_router.config_loader import load_config
from src.llm_router.env import env_setup
from src.llm_router.vllm_launcher import build_cli_args_from_dict

logger = init_logger(__name__)

running_processes: Dict[str, subprocess.Popen] = {}

def wait_for_model_ready(log_path, timeout=600, model_name=""):
    start_time = time.time()
    last_log_time = 0
    while time.time() - start_time < timeout:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                logs = f.read()
                if ("Started server process" in logs and
                    "Waiting for application startup." in logs and
                    "Application startup complete." in logs):
                    logger.info(f"{model_name} 已偵測到完整啟動訊號。")
                    return True
        if time.time() - last_log_time > 10:
            logger.info(f"仍在等待 {model_name} 啟動中...")
            last_log_time = time.time()
        time.sleep(2)
    return False


def launch_all_models(config_path):
    env_setup()
    config = load_config(path=config_path)
    engines = config.get("LLM_engines", {})

    if not engines:
        logger.warning("未設定任何 LLM 引擎，跳過啟動模型。")
    else:
        logger.info(f"找到 {len(engines)} 個 LLM 引擎，準備啟動...")
    for model_name, model_group_cfg in engines.items():
        try:
            instances = model_group_cfg.get("instances", [])
            shared_model_cfg = model_group_cfg.get("model_config", {})
            if not instances:
                logger.info(f"Model group '{model_name}' has no instances defined.")
                continue
            logger.info(f"Model group '{model_name}' has {len(instances)} instance(s).")
            
            for instance in instances:
                try:
                    instance_id = instance.get("id")
                    if not instance_id:
                        raise ValueError(f"{model_name} 的 instance 缺少 id 欄位")
                    merged_cfg = copy.deepcopy(shared_model_cfg)
                    merged_cfg.update(copy.deepcopy(instance))
                    
                    cuda_id = None
                    if merged_cfg.get("tensor_parallel_size", 1) == 1:
                        cuda_id = merged_cfg.pop("cuda_device", None)
                        
                    # pop id
                    merged_cfg.pop("id", None)

                    cli_args = build_cli_args_from_dict(merged_cfg)

                    logger.info(
                        f"執行指令 [{model_name}/{instance_id}]: vllm {' '.join(cli_args)}"
                    )
                    cuda_env = os.environ.copy()
                    if cuda_id is not None:
                        cuda_env["CUDA_VISIBLE_DEVICES"] = str(cuda_id)
                        logger.info(
                            f"設定 {model_name}/{instance_id} 使用 GPU {cuda_id}"
                        )

                    log_path = f"./logs/{model_name}__{instance_id}.log"
                    os.makedirs(os.path.dirname(log_path), exist_ok=True)
                    
                    if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
                        logger.info(
                            f"{model_name}/{instance_id} 的 log 檔案已存在且不為空，將清空。"
                        )
                        with open(log_path, "w", encoding="utf-8") as f:
                            f.truncate(0)

                    with open(log_path, "w", encoding="utf-8") as log_file:
                        proc = subprocess.Popen(
                            ["vllm"] + cli_args,
                            env=cuda_env,
                            stdout=log_file,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )

                    process_key = f"{model_name}::{instance_id}"
                    running_processes[process_key] = proc

                    logger.info(f"等待 {model_name}/{instance_id} 啟動完成...")
                    if wait_for_model_ready(log_path, model_name=f"{model_name}/{instance_id}"):
                        logger.info(f"{model_name}/{instance_id} 啟動完成。")
                    else:
                        logger.warning(f"{model_name}/{instance_id} 啟動超時，可能啟動失敗。")

                except Exception as e:
                    logger.error(f"處理 {model_name} 的 instance 時發生錯誤: {e}")
        except Exception as e:
            logger.error(f"處理模型群組 {model_name} 時發生錯誤: {e}")   
              
    # embedding server
    embedding_server_cfg = config.get("embedding_server", {})
    has_embedding = bool(embedding_server_cfg.get("embedding_models"))
    has_reranking = bool(embedding_server_cfg.get("reranking_models"))
    if has_embedding or has_reranking:
        try:
            logger.info("啟動 Embedding / Reranker Server ...")

            cuda_env = os.environ.copy()
            cuda_device = embedding_server_cfg.get("cuda_device")
            if cuda_device is not None:
                cuda_env["CUDA_VISIBLE_DEVICES"] = str(cuda_device)
                logger.warning(f"設定 Embedding Server 使用 GPU {cuda_device}")

            proc = subprocess.Popen([
                sys.executable, "-m", "embedding_reranker_server.embedding_reranker_launcher",
                "--config", config_path
            ], env=cuda_env, start_new_session=True)

            running_processes["embedding_server"] = proc
        except Exception as e:
            logger.error(f"啟動 Embedding Server 失敗: {e}")
    else:
        logger.warning("未設定任何 embedding_models 或 reranking_models，略過啟動 Embedding Server。")
    
            
def shutdown_all_models():
    logger.info("關閉所有模型...")
    for model_name, proc in running_processes.items():
        try:
            logger.warning(f"   → 正在關閉 {model_name} (PID={proc.pid})")
            proc.terminate()
            proc.wait(timeout=5)
        except Exception as e:
            logger.error(f"   關閉 {model_name} 失敗: {e}")
    sys.exit(0)
    
    
def main(config_path):
    signal.signal(signal.SIGINT, lambda sig, frame: shutdown_all_models())
    signal.signal(signal.SIGTERM, lambda sig, frame: shutdown_all_models())

    launch_all_models(config_path)

    while True:
        time.sleep(10)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="./configs/config.yaml")
    args = parser.parse_args()

    config_path = args.config
    main(config_path)

            
    

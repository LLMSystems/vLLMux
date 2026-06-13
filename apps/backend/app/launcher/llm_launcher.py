import copy
import json
import os
import subprocess
import logging
from typing import Dict
import time

import yaml

logger = logging.getLogger(__name__)

def wait_for_model_ready(log_path: str, timeout: int = 300, model_name: str = "") -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                logs = f.read()
                if all(kw in logs for kw in [
                    "Started server process",
                    "Waiting for application startup.",
                    "Application startup complete."
                ]):
                    logger.info(f"模型 {model_name} 啟動完成")
                    return True
                if "Traceback" in logs:
                    logger.error(f"模型 {model_name} 啟動失敗，請檢查日誌：{log_path}")
                    return False
        logger.info(f"檢查中：{model_name} 尚未啟動完成，等待中...")
        time.sleep(10)

    logger.error(f"模型 {model_name} 啟動逾時，請檢查日誌：{log_path}")
    return False

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

def launch_single_llm_model(app, model_name: str, config_path: str):
    """
    model_name : Qwen3-0.6B::qwen3-2
    seperate model group name and instance id by "::", if no instance id, just use model group name as key in running_llm_procs. This allows us to support both single-instance models and multi-instance models with the same code.
    """
    
    running_llm_procs = app.state.running_llm_procs
    if model_name in running_llm_procs and running_llm_procs[model_name].poll() is None:
        logger.info(f"模型 {model_name} 已經在執行中，跳過啟動。")
        raise RuntimeError(f"模型 {model_name} 已在執行中，請勿重複啟動。")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    engines = config.get("LLM_engines", {})
    
    # seperate model group name and instance id by "::"
    model_tag = model_name.split("::")[0]
    model_id = model_name.split("::")[1] if "::" in model_name else None
    if model_tag not in engines:
        raise ValueError(f"模型群組 {model_tag} 未在配置中找到。")
    if model_id is None:
        logger.info(f"啟動模型 {model_name}，未指定 instance id，將啟動 {model_tag} 群組下的所有實例。")
    
    model_group_cfg = copy.deepcopy(engines[model_tag])
    
    try:
        instances = model_group_cfg.get("instances", [])
        shared_model_cfg = model_group_cfg.get("model_config", {})
        if not instances:
            logger.info(f"Model group '{model_name}' has no instances defined.")
            # skip launching if no instances defined, but still create an empty entry in running_llm_procs to prevent future launches
            running_llm_procs[model_name] = None    
            raise RuntimeError(f"模型 {model_name} 沒有定義任何實例，無法啟動。")
        else:
            logger.info(f"Model group '{model_name}' has {len(instances)} instance(s).")
            for instance in instances:
                try:
                    instance_id = instance.get("id")
                    if model_id and instance_id != model_id:
                        logger.info(f"跳過 {model_name} 的 instance {instance_id}，因為它不匹配請求的 instance id {model_id}")
                        continue
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
                            f"設定 {model_tag}/{instance_id} 使用 GPU {cuda_id}"
                        )

                    log_path = f"./logs/{model_tag}__{instance_id}.log"
                    os.makedirs(os.path.dirname(log_path), exist_ok=True)
                    
                    if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
                        logger.info(
                            f"{model_tag}/{instance_id} 的 log 檔案已存在且不為空，將清空。"
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

                    process_key = f"{model_tag}::{instance_id}"
                    running_llm_procs[process_key] = proc
                    app.state.starting_models.add(process_key)

                    logger.info(f"等待 {model_tag}/{instance_id} 啟動完成...")
                    if wait_for_model_ready(log_path, model_name=f"{model_tag}/{instance_id}"):
                        logger.info(f"{model_tag}/{instance_id} 啟動完成。")
                        app.state.starting_models.discard(process_key)
                    else:
                        logger.warning(f"{model_tag}/{instance_id} 啟動超時，可能啟動失敗。")
                        app.state.starting_models.discard(process_key)
                except Exception as e:
                    logger.error(f"處理 {model_tag} 的 instance 時發生錯誤: {e}")
    except Exception as e:
        logger.error(f"啟動模型 {model_tag} 時發生錯誤: {e}")
        running_llm_procs[model_tag] = None
        raise
    
    
def stop_single_llm_model(app, model_name: str):
    running_llm_procs = app.state.running_llm_procs
    
    model_tag = model_name.split("::")[0]
    model_id = model_name.split("::")[1] if "::" in model_name else None
    if model_id is None:
        logger.info(f"啟動模型 {model_name}，未指定 instance id，將關閉 {model_tag} 群組下的所有實例。")
        keys_to_stop = [key for key in running_llm_procs if key.startswith(model_tag + "::")]
    else:
        logger.info(f"停止模型 {model_name}，將只停止 instance id 為 {model_id} 的實例。")
        keys_to_stop = [model_name] if model_name in running_llm_procs else []
        
    if not keys_to_stop:
        logger.info(f"沒有找到任何以 {model_name} 開頭的運行中模型實例。")
        
    for key in keys_to_stop:
        proc = running_llm_procs.get(key)
        if proc and proc.poll() is None:
            logger.info(f"關閉模型實例 {key} (PID={proc.pid})")
            proc.terminate()
            try:
                proc.wait(timeout=5)
                logger.info(f"模型實例 {key} 已成功關閉。")
                app.state.starting_models.discard(key)
            except subprocess.TimeoutExpired:
                logger.warning(f"模型實例 {key} 關閉超時，強制終止。")
                proc.kill()
                proc.wait()
                app.state.starting_models.discard(key)
            del running_llm_procs[key]
        else:
            logger.info(f"模型實例 {key} 沒有在執行中。")   
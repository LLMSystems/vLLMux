import os
import subprocess
import sys
import time
from typing import Optional

import yaml

llm_server_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../router-server"))

running_proc: Optional[subprocess.Popen] = None

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
                    print(f"模型 {model_name} 啟動完成")
                    return True
                if "Traceback" in logs:
                    print(f"模型 {model_name} 啟動失敗，請檢查日誌：{log_path}")
                    return False
        print(f"檢查中：{model_name} 尚未啟動完成，等待中...")
        time.sleep(10)

    print(f"模型 {model_name} 啟動逾時，請檢查日誌：{log_path}")
    return False

def launch_embedding_server(config_path: str):
    global running_proc
    
    if running_proc is not None and running_proc.poll() is None:
        print("Embedding Server 已經在執行中，跳過啟動。")
        raise RuntimeError("Embedding Server 已在執行中")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    embedding_cfg = config.get("embedding_server", {})

    has_embedding = bool(embedding_cfg.get("embedding_models"))
    has_reranking = bool(embedding_cfg.get("reranking_models"))
    
    if not has_embedding and not has_reranking:
        print("未設定任何 embedding 或 reranking 模型，略過啟動。")
        raise RuntimeError("未設定任何 embedding 或 reranking 模型")
    print("啟動 Embedding / Reranker Server ...")
    cuda_device = embedding_cfg.get("cuda_device")
    cuda_env = os.environ.copy()
    if cuda_device is not None:
        cuda_env["CUDA_VISIBLE_DEVICES"] = str(cuda_device)
        print(f"設定使用 GPU：{cuda_device}")
    cuda_env["PYTHONPATH"] = llm_server_root
        
    log_path = f"./logs/embedding_server.log"
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    
    # 清空之前的日誌
    with open(log_path, "w", encoding="utf-8") as f:
        print(f"清空日誌檔案：{log_path}")
        f.truncate(0)
    
    with open(log_path, "w", encoding="utf-8") as log_file:
        try:
            ## TODO: 檢查是否已經有正在運行的實例
            running_proc = subprocess.Popen([
                sys.executable,
                os.path.join(llm_server_root, "src", "embedding_reranker", "embedding_reranker_launcher.py"),
                "--config",
                config_path
            ], env=cuda_env, stderr=subprocess.STDOUT, start_new_session=True, stdout=log_file)
            print(f"Embedding Server 啟動中 (PID={running_proc.pid})")
        except Exception as e:
            raise RuntimeError(f"啟動 Embedding Server 失敗: {e}")
        if not wait_for_model_ready(log_path, timeout=30, model_name='Embedding & reranking Server'):
            raise RuntimeError(f"Embedding & reranking Server 啟動失敗，請檢查日誌 {log_path}")
        time.sleep(5) 
        if running_proc.poll() is not None:
            raise RuntimeError("Embedding Server 啟動失敗，請檢查日誌檔案。")

def stop_embedding_server():
    global running_proc
    if running_proc and running_proc.poll() is None:
        print(f"關閉 Embedding Server (PID={running_proc.pid})")
        running_proc.terminate()
        running_proc.wait(timeout=5)
    else:
        print("沒有正在執行的 Embedding Server")
    
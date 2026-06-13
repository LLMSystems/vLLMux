"""GPU inspection via nvidia-smi.

Moved out of routes/system.py so that route handlers no longer own business
logic and so main.py's background task can import a service instead of a route.
"""
import csv
import io
import logging
import subprocess

import psutil

logger = logging.getLogger(__name__)


def get_gpu_info() -> list[dict]:
    try:
        result = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            encoding="utf-8",
        )
        lines = result.strip().split("\n")
        gpus = []
        for line in lines:
            index, name, mem_used, mem_total, util = line.split(", ")
            gpus.append(
                {
                    "index": int(index),
                    "name": name,
                    "memory_used": int(mem_used),
                    "memory_total": int(mem_total),
                    "gpu_util": int(util),
                }
            )
        return gpus
    except Exception:
        return []


def get_gpu_processes_with_info() -> list[dict]:
    cmd = [
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )

    if result.returncode != 0:
        logger.error("Error: %s", result.stderr)
        return []

    reader = csv.reader(io.StringIO(result.stdout))
    processes = []

    for row in reader:
        if len(row) != 3:
            continue

        pid_str, name, mem_str = row
        pid_str, name, mem_str = pid_str.strip(), name.strip(), mem_str.strip()

        try:
            pid = int(pid_str)
        except ValueError:
            continue

        try:
            used_mem = int(mem_str)
        except ValueError:
            used_mem = None

        process_info = {
            "pid": pid,
            "nvidia_smi_name": name,
            "used_memory_mib": used_mem,
        }

        try:
            p = psutil.Process(pid)
            process_info.update(
                {
                    "exe": p.exe(),
                    "name": p.name(),
                    "cmdline": p.cmdline(),
                    "username": p.username(),
                }
            )
        except psutil.NoSuchProcess:
            process_info["error"] = "No such process"
        except Exception as e:
            process_info["error"] = str(e)

        processes.append(process_info)

    processes_sorted = sorted(
        processes,
        key=lambda x: (x["used_memory_mib"] is None, -(x["used_memory_mib"] or 0)),
    )

    return processes_sorted

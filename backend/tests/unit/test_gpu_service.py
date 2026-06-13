import subprocess

import psutil
import pytest

from app.services import gpu_service

pytestmark = pytest.mark.unit


class _FakeCompleted:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_get_gpu_info_parses_csv(monkeypatch):
    csv_out = "0, NVIDIA RTX 3060 Ti, 1024, 8192, 37\n"
    monkeypatch.setattr(gpu_service.subprocess, "check_output", lambda *a, **k: csv_out)
    gpus = gpu_service.get_gpu_info()
    assert gpus == [
        {
            "index": 0,
            "name": "NVIDIA RTX 3060 Ti",
            "memory_used": 1024,
            "memory_total": 8192,
            "gpu_util": 37,
        }
    ]


def test_get_gpu_info_returns_empty_on_failure(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("nvidia-smi not found")

    monkeypatch.setattr(gpu_service.subprocess, "check_output", boom)
    assert gpu_service.get_gpu_info() == []


def test_processes_sorted_by_memory_desc_with_none_last(monkeypatch):
    stdout = "111, python, 500\n222, vllm, 2048\n333, ghost, N/A\n"
    monkeypatch.setattr(
        gpu_service.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(0, stdout=stdout),
    )

    # Avoid touching the real process table.
    def fake_process(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(gpu_service.psutil, "Process", fake_process)

    procs = gpu_service.get_gpu_processes_with_info()
    assert [p["pid"] for p in procs] == [222, 111, 333]  # 2048, 500, then None last
    assert procs[2]["used_memory_mib"] is None
    assert all(p["error"] == "No such process" for p in procs)


def test_processes_empty_on_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(
        gpu_service.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(9, stderr="boom"),
    )
    assert gpu_service.get_gpu_processes_with_info() == []

"""Pure-disk hf_service helpers (no network)."""
import os

import pytest

from app.services import hf_service

pytestmark = pytest.mark.unit


def test_repo_cache_dir_naming(monkeypatch):
    monkeypatch.setattr(hf_service, "_cache_root", lambda: "/cache")
    assert hf_service.repo_cache_dir("Qwen/Qwen3-0.6B") == "/cache/models--Qwen--Qwen3-0.6B"


def test_repo_dir_size_sums_files_including_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(hf_service, "_cache_root", lambda: str(tmp_path))
    repo = tmp_path / "models--org--m" / "blobs"
    repo.mkdir(parents=True)
    (repo / "a").write_bytes(b"x" * 100)
    (repo / "b.incomplete").write_bytes(b"y" * 50)  # in-progress bytes count too
    assert hf_service.repo_dir_size("org/m") == 150


def test_repo_dir_size_zero_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(hf_service, "_cache_root", lambda: str(tmp_path))
    assert hf_service.repo_dir_size("org/never") == 0


def test_disk_usage_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(hf_service, "_cache_root", lambda: str(tmp_path))
    usage = hf_service.disk_usage()
    assert {"total", "used", "free"} <= usage.keys()
    assert usage["total"] >= usage["free"]
    assert os.path.exists(str(tmp_path))

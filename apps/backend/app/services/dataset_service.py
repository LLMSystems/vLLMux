"""Thin wrapper over ModelScope's dataset cache for the evalscope perf datasets.

evalscope's perf datasets download via ModelScope (``dataset_snapshot_download``)
into ``$MODELSCOPE_CACHE/datasets/<dataset_id>/<file>`` — a different cache from
the HF weights cache. This module lists / pre-downloads / deletes the handful of
datasets the dashboard's benchmark can use, so a benchmark run doesn't stall on a
first-time 460 MB download. Synchronous (disk/network IO) — callers thread it.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Optional

# Curated catalog: the downloadable datasets the benchmark UI actually uses.
# (random_* datasets generate locally and speed_benchmark is in-code — neither
# downloads, so they're not listed here.) key -> ModelScope dataset id + file.
CATALOG: list[dict[str, str]] = [
    {
        "key": "share_gpt_zh",
        "label": "ShareGPT 中文",
        "dataset_id": "swift/sharegpt",
        "file": "common_zh_70k.jsonl",
        "note": "多輪對話壓測 share_gpt_zh_multi_turn 用",
        "approx": "~460 MB",
    },
    {
        "key": "share_gpt_en",
        "label": "ShareGPT 英文",
        "dataset_id": "swift/sharegpt",
        "file": "common_en_70k.jsonl",
        "note": "多輪對話壓測 share_gpt_en_multi_turn 用",
        "approx": "~500 MB",
    },
    {
        "key": "openqa",
        "label": "OpenQA（HC3 中文）",
        "dataset_id": "AI-ModelScope/HC3-Chinese",
        "file": "open_qa.jsonl",
        "note": "單輪 openqa 資料集",
        "approx": "~數 MB",
    },
]

_BY_KEY = {d["key"]: d for d in CATALOG}


def get_entry(key: str) -> Optional[dict[str, str]]:
    return _BY_KEY.get(key)


def _cache_root() -> str:
    """ModelScope dataset cache root. Mirrors modelscope's own resolution
    (``$MODELSCOPE_CACHE`` or ``~/.cache/modelscope/hub``, then ``/datasets``)
    without importing modelscope — so listing the cache works even where the
    heavy package isn't installed (e.g. the lightweight CI test job)."""
    base = os.environ.get("MODELSCOPE_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "modelscope", "hub"
    )
    return os.path.join(os.path.expanduser(base), "datasets")


def file_path(entry: dict[str, str]) -> str:
    """On-disk path of a dataset's file (may not exist yet)."""
    return os.path.join(_cache_root(), entry["dataset_id"], entry["file"])


def dataset_dir(entry: dict[str, str]) -> str:
    return os.path.join(_cache_root(), entry["dataset_id"])


def cached_size(entry: dict[str, str]) -> int:
    """Bytes on disk for the dataset's file (0 if not downloaded)."""
    try:
        return os.path.getsize(file_path(entry))
    except OSError:
        return 0


def dir_size(entry: dict[str, str]) -> int:
    """Bytes under the dataset dir incl. in-flight temp files (smooth progress)."""
    total = 0
    for root, _dirs, files in os.walk(dataset_dir(entry)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def download(entry: dict[str, str]) -> str:
    """Blocking ModelScope download of just this dataset's file. Returns its path."""
    from modelscope import dataset_snapshot_download

    local = dataset_snapshot_download(entry["dataset_id"], allow_patterns=[entry["file"]])
    return os.path.join(local, entry["file"])


def disk_usage() -> dict[str, int]:
    """total/used/free bytes of the volume holding the dataset cache."""
    root = _cache_root()
    probe = root
    while probe and not os.path.exists(probe):
        probe = os.path.dirname(probe)
    usage = shutil.disk_usage(probe or "/")
    return {"total": usage.total, "used": usage.used, "free": usage.free}


def scan() -> list[dict[str, Any]]:
    """Catalog with per-dataset cached flag + on-disk size."""
    out: list[dict[str, Any]] = []
    for e in CATALOG:
        size = cached_size(e)
        out.append({**e, "cached": size > 0, "size_on_disk": size})
    return out


def delete(key: str) -> bool:
    """Remove a cached dataset file. False if it wasn't cached."""
    entry = _BY_KEY.get(key)
    if entry is None:
        return False
    path = file_path(entry)
    if not os.path.exists(path):
        return False
    os.remove(path)
    # Tidy now-empty dataset dir (best-effort).
    try:
        os.removedirs(os.path.dirname(path))
    except OSError:
        pass
    return True

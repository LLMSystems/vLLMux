"""Thin wrapper over huggingface_hub for weight management.

All functions are synchronous (and some do network/disk IO) — callers run them
in a thread executor. The HF cache they read/write is the same one vLLM uses
(HF_HOME), so a model downloaded here is already local when its instance starts.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Optional


def _cache_root() -> str:
    from huggingface_hub.constants import HF_HUB_CACHE

    return HF_HUB_CACHE


def repo_cache_dir(repo_id: str) -> str:
    """Filesystem path of a repo's cache folder (may not exist yet)."""
    folder = "models--" + repo_id.replace("/", "--")
    return os.path.join(_cache_root(), folder)


def repo_dir_size(repo_id: str) -> int:
    """Bytes currently on disk for a repo, including in-progress `.incomplete`
    blobs — so download progress is smooth rather than jumping at the end."""
    path = repo_cache_dir(repo_id)
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def model_total_size(repo_id: str, token: Optional[str] = None) -> Optional[int]:
    """Total download size of a repo from the Hub, or None if unknown."""
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo_id, files_metadata=True, token=token)
    sizes = [getattr(s, "size", None) for s in (info.siblings or [])]
    known = [s for s in sizes if s]
    return sum(known) if known else None


def download(repo_id: str, token: Optional[str] = None) -> str:
    """Blocking snapshot download into the shared HF cache. Returns the path."""
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id, token=token)


def disk_usage() -> dict[str, int]:
    """total/used/free bytes of the volume holding the HF cache."""
    root = _cache_root()
    probe = root if os.path.exists(root) else os.path.dirname(root) or "/"
    usage = shutil.disk_usage(probe)
    return {"total": usage.total, "used": usage.used, "free": usage.free}


def scan_cache() -> list[dict[str, Any]]:
    """Cached models with on-disk size + revision hashes (for deletion)."""
    from huggingface_hub import scan_cache_dir

    try:
        info = scan_cache_dir()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for repo in info.repos:
        if repo.repo_type != "model":
            continue
        out.append({
            "repo_id": repo.repo_id,
            "size_on_disk": repo.size_on_disk,
            "nb_files": repo.nb_files,
            "last_modified": repo.last_modified,
            "revisions": [r.commit_hash for r in repo.revisions],
        })
    out.sort(key=lambda r: r["size_on_disk"], reverse=True)
    return out


def delete_repo(repo_id: str) -> bool:
    """Delete every cached revision of a model. False if it wasn't cached."""
    from huggingface_hub import scan_cache_dir

    info = scan_cache_dir()
    hashes = [
        r.commit_hash
        for repo in info.repos
        if repo.repo_id == repo_id and repo.repo_type == "model"
        for r in repo.revisions
    ]
    if not hashes:
        return False
    info.delete_revisions(*hashes).execute()
    return True

"""In-memory manager for HF weight downloads.

Each download runs the blocking snapshot in a thread while a poller samples the
repo's on-disk size for a smooth progress percentage. Jobs are kept in memory
(history is not persisted) — the dashboard polls `list()` while one is active.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from app.services import hf_service

logger = logging.getLogger(__name__)


@dataclass
class DownloadJob:
    repo_id: str
    state: str = "pending"  # pending | downloading | completed | failed
    total_bytes: Optional[int] = None
    downloaded_bytes: int = 0
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class DownloadManager:
    def __init__(self, token: Optional[str] = None) -> None:
        self._jobs: dict[str, DownloadJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._token = token or os.environ.get("HF_TOKEN") or None

    def list(self) -> list[dict]:
        return [asdict(j) for j in sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)]

    def get(self, repo_id: str) -> Optional[dict]:
        job = self._jobs.get(repo_id)
        return asdict(job) if job else None

    def start(self, repo_id: str) -> dict:
        repo_id = repo_id.strip()
        if not repo_id:
            raise ValueError("repo_id is required")
        existing = self._jobs.get(repo_id)
        if existing and existing.state in ("pending", "downloading"):
            return asdict(existing)  # already in flight — idempotent
        job = DownloadJob(repo_id=repo_id)
        self._jobs[repo_id] = job
        self._tasks[repo_id] = asyncio.create_task(self._run(job))
        return asdict(job)

    async def _run(self, job: DownloadJob) -> None:
        loop = asyncio.get_event_loop()
        job.state = "downloading"
        job.updated_at = time.time()
        poller: Optional[asyncio.Task] = None
        try:
            try:
                job.total_bytes = await loop.run_in_executor(
                    None, hf_service.model_total_size, job.repo_id, self._token
                )
            except Exception:
                job.total_bytes = None  # size lookup is best-effort

            poller = asyncio.create_task(self._poll(job))
            await loop.run_in_executor(None, hf_service.download, job.repo_id, self._token)

            job.downloaded_bytes = await loop.run_in_executor(
                None, hf_service.repo_dir_size, job.repo_id
            )
            if job.total_bytes:
                job.total_bytes = max(job.total_bytes, job.downloaded_bytes)
            job.state = "completed"
            logger.info("Downloaded %s (%d bytes)", job.repo_id, job.downloaded_bytes)
        except Exception as e:
            job.state = "failed"
            job.error = str(e)
            logger.exception("Download failed for %s", job.repo_id)
        finally:
            if poller is not None:
                poller.cancel()
            job.updated_at = time.time()
            self._tasks.pop(job.repo_id, None)

    async def _poll(self, job: DownloadJob) -> None:
        loop = asyncio.get_event_loop()
        try:
            while True:
                job.downloaded_bytes = await loop.run_in_executor(
                    None, hf_service.repo_dir_size, job.repo_id
                )
                job.updated_at = time.time()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

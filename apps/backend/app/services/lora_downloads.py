"""In-memory manager for LoRA adapter downloads (mirrors DownloadManager).

Each job snapshots an adapter repo into <lora_root>/<name> while a poller samples
the folder size for a smooth percentage. Keyed by the local adapter name (so two
repos can't clobber the same folder mid-flight). History is not persisted.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from app.services import hf_service, lora_service

logger = logging.getLogger(__name__)


@dataclass
class LoraDownloadJob:
    name: str
    repo_id: str
    state: str = "pending"  # pending | downloading | completed | failed
    total_bytes: Optional[int] = None
    downloaded_bytes: int = 0
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class LoraDownloadManager:
    def __init__(self, token: Optional[str] = None) -> None:
        self._jobs: dict[str, LoraDownloadJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._token = token or os.environ.get("HF_TOKEN") or None

    def list(self) -> list[dict]:
        return [asdict(j) for j in sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)]

    def start(self, repo_id: str, name: Optional[str] = None) -> dict:
        repo_id = repo_id.strip()
        if not repo_id:
            raise ValueError("repo_id is required")
        # Default the local folder to the repo leaf (e.g. org/sql-lora -> sql-lora).
        name = (name or repo_id.split("/")[-1]).strip()
        # Validate the folder name early (raises ValueError on traversal/garbage).
        lora_service.adapter_dir(name)
        existing = self._jobs.get(name)
        if existing and existing.state in ("pending", "downloading"):
            return asdict(existing)  # already in flight — idempotent
        job = LoraDownloadJob(name=name, repo_id=repo_id)
        self._jobs[name] = job
        self._tasks[name] = asyncio.create_task(self._run(job))
        return asdict(job)

    async def _run(self, job: LoraDownloadJob) -> None:
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
            await loop.run_in_executor(
                None, lora_service.download, job.repo_id, job.name, self._token
            )

            job.downloaded_bytes = await loop.run_in_executor(
                None, lora_service.dir_size, lora_service.adapter_dir(job.name)
            )
            if job.total_bytes:
                job.total_bytes = max(job.total_bytes, job.downloaded_bytes)
            job.state = "completed"
            logger.info("Downloaded LoRA %s -> %s (%d bytes)", job.repo_id, job.name, job.downloaded_bytes)
        except Exception as e:
            job.state = "failed"
            job.error = str(e)
            logger.exception("LoRA download failed for %s", job.repo_id)
        finally:
            if poller is not None:
                poller.cancel()
            job.updated_at = time.time()
            self._tasks.pop(job.name, None)

    async def _poll(self, job: LoraDownloadJob) -> None:
        loop = asyncio.get_event_loop()
        try:
            folder = lora_service.adapter_dir(job.name)
            while True:
                job.downloaded_bytes = await loop.run_in_executor(None, lora_service.dir_size, folder)
                job.updated_at = time.time()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

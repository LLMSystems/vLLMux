"""In-memory manager for ModelScope dataset downloads (mirrors DownloadManager).

Each download runs the blocking snapshot in a thread while a poller samples the
dataset dir's on-disk size for progress. ModelScope doesn't expose a reliable
upfront size, so ``total_bytes`` stays None and the UI shows bytes + a spinner.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from app.services import dataset_service

logger = logging.getLogger(__name__)


@dataclass
class DatasetDownloadJob:
    key: str
    label: str
    state: str = "pending"  # pending | downloading | completed | failed
    total_bytes: Optional[int] = None
    downloaded_bytes: int = 0
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # True while the post-download evalscope cache warm is still running, so the
    # UI can hold "preview" until the first load is actually fast.
    warming: bool = False


class DatasetDownloadManager:
    def __init__(self) -> None:
        self._jobs: dict[str, DatasetDownloadJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def list(self) -> list[dict]:
        return [asdict(j) for j in sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)]

    def start(self, key: str) -> dict:
        entry = dataset_service.get_entry(key)
        if entry is None:
            raise ValueError(f"unknown dataset: {key}")
        existing = self._jobs.get(key)
        if existing and existing.state in ("pending", "downloading"):
            return asdict(existing)  # already in flight — idempotent
        job = DatasetDownloadJob(key=key, label=entry["label"])
        self._jobs[key] = job
        self._tasks[key] = asyncio.create_task(self._run(job, entry))
        return asdict(job)

    async def _run(self, job: DatasetDownloadJob, entry: dict) -> None:
        loop = asyncio.get_event_loop()
        job.state = "downloading"
        job.updated_at = time.time()
        poller: Optional[asyncio.Task] = None
        try:
            poller = asyncio.create_task(self._poll(job, entry))
            await loop.run_in_executor(None, dataset_service.download, entry)
            job.downloaded_bytes = await loop.run_in_executor(None, dataset_service.cached_size, entry)
            job.state = "completed"
            logger.info("Downloaded dataset %s (%d bytes)", job.key, job.downloaded_bytes)
            # Eval datasets pay a one-time ~30s online resolution on their first
            # evalscope load. Warm it now (in the background) so the first preview /
            # eval run is instant. Best-effort — never affects the download result.
            if entry.get("category") == "eval":
                job.warming = True
                asyncio.create_task(self._warm_eval(job))
        except Exception as e:
            job.state = "failed"
            job.error = str(e)
            logger.exception("Dataset download failed for %s", job.key)
        finally:
            if poller is not None:
                poller.cancel()
            job.updated_at = time.time()
            self._tasks.pop(job.key, None)

    async def _warm_eval(self, job: DatasetDownloadJob) -> None:
        """Trigger evalscope's one-time cold load so the first preview/eval is fast.
        Loads a single row; the cost is the dataset-level online resolution, which
        then caches for every later load. Failures are logged, not raised."""
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, dataset_service.eval_dataset_preview, job.key, None, 1)
            logger.info("Warmed eval dataset cache for %s", job.key)
        except Exception:
            logger.warning("Eval dataset warm failed for %s (non-fatal)", job.key, exc_info=True)
        finally:
            job.warming = False
            job.updated_at = time.time()

    async def _poll(self, job: DatasetDownloadJob, entry: dict) -> None:
        loop = asyncio.get_event_loop()
        try:
            while True:
                job.downloaded_bytes = await loop.run_in_executor(None, dataset_service.dir_size, entry)
                job.updated_at = time.time()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

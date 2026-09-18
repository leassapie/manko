"""Download queue system with concurrency control.

Manages per-user and global download queues to prevent overload.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger("mangko")


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadJob:
    user_id: int
    url: str
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None


class DownloadQueue:
    """Global download queue with per-user limits."""

    def __init__(self, max_global: int = 5, max_per_user: int = 1) -> None:
        self._max_global = max_global
        self._max_per_user = max_per_user
        self._queue: asyncio.Queue[DownloadJob] = asyncio.Queue()
        self._active: dict[int, int] = {}  # user_id → count
        self._global_active = 0
        self._lock = asyncio.Lock()
        self._running = False
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the queue worker."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("Download queue started (max_global=%d)", self._max_global)

    async def stop(self) -> None:
        """Stop the queue worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        logger.info("Download queue stopped")

    async def enqueue(
        self,
        user_id: int,
        url: str,
        download_fn: object,
        *args: object,
        **kwargs: object,
    ) -> DownloadJob:
        """Add a download job to the queue."""
        job = DownloadJob(user_id=user_id, url=url)
        await self._queue.put(job)
        logger.info("Job enqueued", user_id=user_id, url=url)
        return job

    async def cancel_user(self, user_id: int) -> int:
        """Cancel all pending jobs for a user. Returns count cancelled."""
        cancelled = 0
        temp: list[DownloadJob] = []
        while not self._queue.empty():
            try:
                job = self._queue.get_nowait()
                if job.user_id == user_id and job.status == JobStatus.PENDING:
                    job.status = JobStatus.CANCELLED
                    cancelled += 1
                else:
                    temp.append(job)
            except asyncio.QueueEmpty:
                break
        for job in temp:
            await self._queue.put(job)
        return cancelled

    def is_user_busy(self, user_id: int) -> bool:
        """Check if user has an active download."""
        return self._active.get(user_id, 0) >= self._max_per_user

    @property
    def stats(self) -> dict:
        """Get queue statistics."""
        return {
            "pending": self._queue.qsize(),
            "global_active": self._global_active,
            "per_user": dict(self._active),
        }

    async def _worker(self) -> None:
        """Process jobs from the queue."""
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            async with self._lock:
                user_count = self._active.get(job.user_id, 0)
                if user_count >= self._max_per_user:
                    await self._queue.put(job)
                    await asyncio.sleep(0.5)
                    continue
                if self._global_active >= self._max_global:
                    await self._queue.put(job)
                    await asyncio.sleep(0.5)
                    continue

                self._active[job.user_id] = user_count + 1
                self._global_active += 1
                job.status = JobStatus.RUNNING
                job.started_at = time.time()

            try:
                # Job execution handled by caller
                # This worker just manages concurrency
                await asyncio.sleep(0.1)
            finally:
                async with self._lock:
                    self._active[job.user_id] = max(
                        0, self._active.get(job.user_id, 1) - 1
                    )
                    self._global_active = max(0, self._global_active - 1)
                    job.completed_at = time.time()
                    if job.status == JobStatus.RUNNING:
                        job.status = JobStatus.COMPLETED


# Global queue instance
download_queue = DownloadQueue()

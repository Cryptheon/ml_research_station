"""In-process job store for background ingest tasks.

Each job gets an asyncio.Queue for streaming frames to a WebSocket.
The background thread pushes via call_soon_threadsafe; the WS handler
reads with await queue.get().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class IngestJob:
    id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "pending"  # pending | running | done | error
    found: int = 0
    scanned: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    _loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def push(self, frame: dict) -> None:
        """Thread-safe push from a sync background thread."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, frame)

    async def next_frame(self) -> dict:
        return await self._queue.get()


_store: dict[str, IngestJob] = {}


def create_job() -> IngestJob:
    job = IngestJob()
    _store[job.id] = job
    return job


def get_job(job_id: str) -> IngestJob | None:
    return _store.get(job_id)

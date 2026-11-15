"""Share asynchronous work for an exact key while at least one caller needs it."""

import asyncio
from collections.abc import Callable, Coroutine, Hashable
from dataclasses import dataclass
from typing import Any


@dataclass
class Job[T]:
    task: asyncio.Task[T]
    subscribers: int = 0


class SharedWork[T]:
    def __init__(self):
        self._jobs: dict[Hashable, Job[T]] = {}

    async def run(self, key: Hashable, load: Callable[[], Coroutine[Any, Any, T]]) -> T:
        job = self._jobs.get(key)
        if job is None:
            job = self._jobs[key] = Job(asyncio.create_task(load()))
        job.subscribers += 1
        try:
            return await asyncio.shield(job.task)
        finally:
            job.subscribers -= 1
            if job.subscribers == 0:
                del self._jobs[key]
                job.task.cancel()
                await asyncio.gather(job.task, return_exceptions=True)

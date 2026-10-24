"""Built-in process-local RunBackend implementation."""

from __future__ import annotations

from typing import AsyncIterator

from core_harness import ControlPlaneEvent, EventSink

from core_server.execution import RunExecutor
from core_server.models import RunSubmission
from core_server.runs import RunManager, RunRecord


class InProcessRunBackend:
    """Schedule requests as local asyncio tasks using an in-memory RunManager."""

    def __init__(
        self,
        executor: RunExecutor,
        *,
        manager: RunManager | None = None,
    ) -> None:
        self.executor = executor
        self.manager = manager or RunManager()

    async def submit(self, submission: RunSubmission) -> RunRecord:
        async def execute(run_id: str, sink: EventSink) -> None:
            await self.executor.execute(run_id, submission, sink)

        return await self.manager.start(submission.context, execute)

    async def get(self, run_id: str) -> RunRecord:
        return await self.manager.get(run_id)

    async def events(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[tuple[int, ControlPlaneEvent]]:
        async for event in self.manager.events(run_id, after=after):
            yield event

    async def cancel(self, run_id: str) -> RunRecord:
        return await self.manager.cancel(run_id)

    async def shutdown(self) -> None:
        await self.manager.shutdown()


__all__ = ["InProcessRunBackend"]

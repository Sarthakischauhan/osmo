"""Connection-independent run execution and replayable event storage."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Protocol

from core_harness import ControlPlaneEvent, EventSink

from core_server.models import RunContext, RunStatus, RunStatusResponse, RunSubmission

RunCoroutine = Callable[[str, EventSink], Awaitable[None]]

_TERMINAL_EVENTS: dict[str, RunStatus] = {
    "run_completed": "completed",
    "run_failed": "failed",
    "run_cancelled": "cancelled",
    "run_limit_exceeded": "failed",
}


@dataclass
class RunRecord:
    run_id: str
    context: RunContext
    status: RunStatus = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    events: list[ControlPlaneEvent] = field(default_factory=list)
    task: Optional[asyncio.Task[None]] = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    def response(self) -> RunStatusResponse:
        return RunStatusResponse(
            run_id=self.run_id,
            session_id=self.context.session_id,
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.error,
        )


class RunState(Protocol):
    """Read-only run state required by HTTP responses and authorization."""

    run_id: str
    context: RunContext
    status: RunStatus

    def response(self) -> RunStatusResponse:
        ...


class RunBackend(Protocol):
    """Storage and scheduling contract consumed by the FastAPI router."""

    async def submit(self, submission: RunSubmission) -> RunState:
        """Accept the serializable request or raise ``RunCapacityError``."""
        ...

    async def get(self, run_id: str) -> RunState:
        """Return current run state."""
        ...

    def events(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[tuple[int, ControlPlaneEvent]]:
        """Replay events after the given one-based transport ordinal."""
        ...

    async def cancel(self, run_id: str) -> RunState:
        """Cancel a run and return its resulting state."""
        ...

    async def shutdown(self) -> None:
        """Release backend resources and stop process-owned work."""
        ...


class RunCapacityError(Exception):
    """Raised when a local backend has reached its configured run capacity."""


class ManagedEventSink(EventSink):
    """Append harness events to a run record without coupling to a client."""

    def __init__(self, record: RunRecord) -> None:
        super().__init__()
        self.record = record
        self.terminal_status: Optional[RunStatus] = None
        self.terminal_error: Optional[str] = None

    async def deliver(self, event_type: str, payload: dict[str, Any]) -> None:
        event = ControlPlaneEvent.typed(event_type, payload)
        async with self.record.condition:
            self.record.events.append(event)
            terminal_status = _TERMINAL_EVENTS.get(event.event_type)
            if terminal_status is not None:
                self.terminal_status = terminal_status
                if terminal_status == "failed":
                    self.terminal_error = str(
                        event.payload.get("message") or "run failed"
                    )
            self.record.condition.notify_all()


class RunNotFoundError(KeyError):
    pass


class RunManager:
    """Own run tasks, serialize sessions, and replay their emitted events."""

    def __init__(
        self,
        *,
        max_concurrent_runs: int | None = 64,
        max_outstanding_runs: int | None = 1024,
    ) -> None:
        if max_concurrent_runs is not None and max_concurrent_runs <= 0:
            raise ValueError("max_concurrent_runs must be positive or None")
        if max_outstanding_runs is not None and max_outstanding_runs <= 0:
            raise ValueError("max_outstanding_runs must be positive or None")
        self._runs: dict[str, RunRecord] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self._max_outstanding_runs = max_outstanding_runs
        self._run_slots = (
            asyncio.Semaphore(max_concurrent_runs)
            if max_concurrent_runs is not None
            else None
        )

    async def start(self, context: RunContext, run: RunCoroutine) -> RunRecord:
        run_id = str(uuid.uuid4())
        record = RunRecord(run_id=run_id, context=context)
        async with self._lock:
            outstanding = sum(
                item.status in {"queued", "running"} for item in self._runs.values()
            )
            if (
                self._max_outstanding_runs is not None
                and outstanding >= self._max_outstanding_runs
            ):
                raise RunCapacityError("run capacity is full")
            self._runs[run_id] = record
            session_lock = self._session_locks.setdefault(
                context.session_id,
                asyncio.Lock(),
            )
            record.task = asyncio.create_task(
                self._execute(record, session_lock, run),
                name=f"core-server-run:{run_id}",
            )
        return record

    async def get(self, run_id: str) -> RunRecord:
        async with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        return record

    async def cancel(self, run_id: str) -> RunRecord:
        record = await self.get(run_id)
        task = record.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return record

    async def events(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[tuple[int, ControlPlaneEvent]]:
        record = await self.get(run_id)
        cursor = max(after, 0)
        while True:
            async with record.condition:
                await record.condition.wait_for(
                    lambda: cursor < len(record.events)
                    or record.status in {"completed", "failed", "cancelled"}
                )
                available = list(enumerate(record.events[cursor:], start=cursor + 1))
                terminal = record.status in {"completed", "failed", "cancelled"}
            for ordinal, event in available:
                cursor = ordinal
                yield ordinal, event
            if terminal and cursor >= len(record.events):
                return

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = [
                record.task
                for record in self._runs.values()
                if record.task is not None and not record.task.done()
            ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(
        self,
        record: RunRecord,
        session_lock: asyncio.Lock,
        run: RunCoroutine,
    ) -> None:
        try:
            async with session_lock:
                if self._run_slots is None:
                    await self._execute_in_slot(record, run)
                else:
                    async with self._run_slots:
                        await self._execute_in_slot(record, run)
        except asyncio.CancelledError:
            if record.status not in {"completed", "failed", "cancelled"}:
                record.status = "cancelled"
                record.finished_at = time.time()
            raise
        except Exception as exc:
            record.status = "failed"
            record.error = str(exc) or type(exc).__name__
            record.finished_at = time.time()
        finally:
            async with record.condition:
                record.condition.notify_all()

    async def _execute_in_slot(
        self,
        record: RunRecord,
        run: RunCoroutine,
    ) -> None:
        record.status = "running"
        record.started_at = time.time()
        sink = ManagedEventSink(record)
        await run(record.run_id, sink)
        if record.status == "running":
            record.status = sink.terminal_status or "completed"
            record.error = sink.terminal_error
            record.finished_at = time.time()


def parse_last_event_id(value: Optional[str], run_id: str) -> int:
    """Return the transport ordinal from ``<run_id>:<ordinal>``."""
    if not value:
        return 0
    event_run_id, separator, ordinal = value.rpartition(":")
    if not separator or event_run_id != run_id:
        return 0
    try:
        return max(int(ordinal), 0)
    except ValueError:
        return 0


__all__ = [
    "ManagedEventSink",
    "RunManager",
    "RunBackend",
    "RunCapacityError",
    "RunNotFoundError",
    "RunRecord",
    "parse_last_event_id",
]

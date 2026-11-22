"""Durable runs: run records, their events, and harness conversations in SQLite.

``DurableRunBackend`` is a ``RunBackend`` like ``InProcessRunBackend``, except that every
submission, status change and event is written to a ``SQLiteRunStore`` as it happens. A
process that stops (crash, kill, upgrade) leaves its unfinished runs marked ``running``;
the next process calls ``recover()`` and they start again on the same run id and session
id, with their event history intact, so a client following ``/runs/{id}/events`` with
``Last-Event-ID`` sees one continuous stream.

The harness picks up where it left off because ``SQLitePersistence`` keeps each
session's conversation (checkpointed after every turn): the resumed run loads it and
gets a short note saying it was interrupted. A tool call that was in flight when the
process died is dropped by ``normalize_tool_protocol`` and is retried by the model if it
still needs it, so tools that change the world should be idempotent or approval gated.

A run that keeps killing the process is not resumed forever: after ``max_attempts``
restarts it is marked failed.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from core_ai.types import Message
from core_harness import Checkpoint, ControlPlaneEvent, EventSink
from core_server.execution import RunExecutor
from core_server.models import RunContext, RunRequest, RunStatus, RunSubmission
from core_server.runs import RunCapacityError, RunManager, RunNotFoundError, RunRecord
from pydantic import TypeAdapter

RUN_RESUMED = "run_resumed"
FINISHED: frozenset[str] = frozenset({"completed", "failed", "cancelled"})
# The status a terminal harness event means (as core_server.runs maps them).
TERMINAL: dict[str, RunStatus] = {"run_completed": "completed", "run_failed": "failed", "run_limit_exceeded": "failed", "run_cancelled": "cancelled"}
TASK_REMINDER_MAX = 2000

RESUME_MESSAGE = (
    "[resumed] The server restarted while you were working, so your last step may not have finished. "
    "Your work so far is above. Check the current state (for example, files you were editing) before "
    "redoing anything, then continue the original task to the end.\n\nOriginal task: {task}"
)

_messages = TypeAdapter(list[Message])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    principal_id TEXT,
    metadata TEXT NOT NULL,
    request TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS runs_status ON runs (status);
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    event TEXT NOT NULL,
    PRIMARY KEY (run_id, ordinal)
);
CREATE TABLE IF NOT EXISTS conversations (
    session_id TEXT PRIMARY KEY,
    messages TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    session_id TEXT PRIMARY KEY,
    checkpoint TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class SQLiteDatabase:
    """One SQLite connection shared by the run store and the persistence (WAL, one writer at a time)."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = NORMAL")
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.executescript(_SCHEMA)

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.connection.execute(sql, parameters).fetchall()

    def close(self) -> None:
        with self.lock:
            self.connection.close()


@dataclass(frozen=True)
class StoredRun:
    run_id: str
    context: RunContext
    request: RunRequest
    status: RunStatus
    error: Optional[str]
    attempts: int
    created_at: float
    started_at: Optional[float]
    finished_at: Optional[float]

    def submission(self) -> RunSubmission:
        return RunSubmission(context=self.context, request=self.request)


class SQLiteRunStore:
    """Run submissions, statuses and events, written as they happen."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.db = database

    def insert(self, run_id: str, submission: RunSubmission, created_at: float) -> None:
        context = submission.context
        self.db.execute(
            "INSERT INTO runs (run_id, session_id, principal_id, metadata, request, status, created_at) VALUES (?, ?, ?, ?, ?, 'queued', ?)",
            (run_id, context.session_id, context.principal_id, json.dumps(dict(context.metadata), default=str), submission.request.model_dump_json(), created_at),
        )

    def set_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: Optional[str] = None,
        started_at: Optional[float] = None,
        finished_at: Optional[float] = None,
    ) -> None:
        self.db.execute(
            "UPDATE runs SET status = ?, error = ?, started_at = COALESCE(?, started_at), finished_at = COALESCE(?, finished_at) WHERE run_id = ?",
            (status, error, started_at, finished_at, run_id),
        )

    def bump_attempts(self, run_id: str) -> int:
        rows = self.db.execute("UPDATE runs SET attempts = attempts + 1 WHERE run_id = ? RETURNING attempts", (run_id,))
        return int(rows[0]["attempts"]) if rows else 0

    def append_events(self, run_id: str, first_ordinal: int, events: list[ControlPlaneEvent]) -> None:
        """Store `events` as ordinals `first_ordinal`, `first_ordinal + 1`, ... (one-based, as replayed)."""
        if not events:
            return
        with self.db.lock:
            self.db.connection.executemany(
                "INSERT OR IGNORE INTO run_events (run_id, ordinal, event) VALUES (?, ?, ?)",
                [(run_id, first_ordinal + offset, event.model_dump_json()) for offset, event in enumerate(events)],
            )

    def events(self, run_id: str) -> list[ControlPlaneEvent]:
        rows = self.db.execute("SELECT event FROM run_events WHERE run_id = ? ORDER BY ordinal", (run_id,))
        return [ControlPlaneEvent.model_validate_json(row["event"]) for row in rows]

    def get(self, run_id: str) -> Optional[StoredRun]:
        rows = self.db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return _stored(rows[0]) if rows else None

    def unfinished(self) -> list[StoredRun]:
        """Runs a previous process accepted and never finished, oldest first."""
        rows = self.db.execute("SELECT * FROM runs WHERE status IN ('queued', 'running') ORDER BY created_at, run_id")
        return [_stored(row) for row in rows]

    def prune(self, finished_before: float) -> int:
        """Forget finished runs (and their events) older than `finished_before` (epoch seconds)."""
        rows = self.db.execute(
            "DELETE FROM runs WHERE status IN ('completed', 'failed', 'cancelled') AND COALESCE(finished_at, created_at) < ? RETURNING run_id",
            (finished_before,),
        )
        return len(rows)


def _stored(row: sqlite3.Row) -> StoredRun:
    return StoredRun(
        run_id=row["run_id"],
        context=RunContext(session_id=row["session_id"], principal_id=row["principal_id"], metadata=json.loads(row["metadata"])),
        request=RunRequest.model_validate_json(row["request"]),
        status=row["status"],
        error=row["error"],
        attempts=int(row["attempts"]),
        created_at=float(row["created_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


class SQLitePersistence:
    """core_harness ``Persistence``: each session's conversation and latest checkpoint, in SQLite.

    The event journal is not kept here (``DurableRunBackend`` stores run events).
    """

    def __init__(self, database: SQLiteDatabase) -> None:
        self.db = database

    async def save_conversation(self, *, session_id: str, messages: list[Message]) -> None:
        payload = _messages.dump_json(list(messages)).decode()
        self.db.execute(
            "INSERT INTO conversations (session_id, messages, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET messages = excluded.messages, updated_at = excluded.updated_at",
            (session_id, payload, time.time()),
        )

    async def load_conversation(self, *, session_id: str) -> list[Message]:
        rows = self.db.execute("SELECT messages FROM conversations WHERE session_id = ?", (session_id,))
        return _messages.validate_json(rows[0]["messages"]) if rows else []

    async def load_resume(self, *, session_id: str) -> tuple[list[Message], list[Message]]:
        messages = await self.load_conversation(session_id=session_id)
        return messages, list(messages)

    async def save_checkpoint(self, *, checkpoint: Checkpoint) -> None:
        self.db.execute(
            "INSERT INTO checkpoints (session_id, checkpoint, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET checkpoint = excluded.checkpoint, updated_at = excluded.updated_at",
            (checkpoint.session_id, checkpoint.model_dump_json(), time.time()),
        )

    async def load_checkpoint(self, *, session_id: str) -> Optional[Checkpoint]:
        rows = self.db.execute("SELECT checkpoint FROM checkpoints WHERE session_id = ?", (session_id,))
        return Checkpoint.model_validate_json(rows[0]["checkpoint"]) if rows else None

    async def append_event(self, *, event_type: str, payload: dict[str, Any]) -> None:
        del event_type, payload

    def prune(self, updated_before: float) -> int:
        """Forget sessions untouched since `updated_before` (epoch seconds)."""
        self.db.execute("DELETE FROM checkpoints WHERE updated_at < ?", (updated_before,))
        return len(self.db.execute("DELETE FROM conversations WHERE updated_at < ? RETURNING session_id", (updated_before,)))


class _AdoptingRunManager(RunManager):
    """A RunManager that can schedule a record it did not create (a resumed run keeps its id and events)."""

    async def adopt(self, record: RunRecord, run: Any) -> RunRecord:
        async with self._lock:
            outstanding = sum(item.status in {"queued", "running"} for item in self._runs.values())
            if self._max_outstanding_runs is not None and outstanding >= self._max_outstanding_runs:
                raise RunCapacityError("run capacity is full")
            self._runs[record.run_id] = record
            session_lock = self._session_locks.setdefault(record.context.session_id, asyncio.Lock())
            record.task = asyncio.create_task(self._execute(record, session_lock, run), name=f"core-server-run:{record.run_id}")
        return record

    def active(self) -> set[str]:
        return {run_id for run_id, record in self._runs.items() if record.task is not None and not record.task.done()}


class DurableRunBackend:
    """``RunBackend`` whose runs outlive the process: stored as they go, resumed by ``recover()``."""

    def __init__(
        self,
        executor: RunExecutor,
        store: SQLiteRunStore,
        *,
        max_concurrent_runs: int | None = 64,
        max_outstanding_runs: int | None = 1024,
        max_attempts: int = 3,
        resume_message: str = RESUME_MESSAGE,
    ) -> None:
        self.executor = executor
        self.store = store
        self.manager = _AdoptingRunManager(max_concurrent_runs=max_concurrent_runs, max_outstanding_runs=max_outstanding_runs)
        self.max_attempts = max_attempts
        self.resume_message = resume_message
        # Runs this process stopped by shutting down. They stay `running` in the store, and what the
        # harness emits while it is torn down (its own `run_cancelled`) is not recorded.
        self._interrupted: set[str] = set()

    async def submit(self, submission: RunSubmission) -> RunRecord:
        record = RunRecord(run_id=str(uuid.uuid4()), context=submission.context)
        self.store.insert(record.run_id, submission, record.created_at)
        try:
            return await self._schedule(record, submission)
        except RunCapacityError:
            self.store.set_status(record.run_id, "failed", error="run capacity is full", finished_at=time.time())
            raise

    async def recover(self) -> list[RunRecord]:
        """Start again every run a previous process left unfinished. Call once, at startup."""
        resumed: list[RunRecord] = []
        for stored in self.store.unfinished():
            events = self.store.events(stored.run_id)
            ended = next((event for event in reversed(events) if event.event_type in TERMINAL), None)
            if ended is not None:
                # It finished; the process stopped before the status was written. Nothing to resume.
                error = str(ended.payload.get("message") or "run failed") if TERMINAL[ended.event_type] == "failed" else None
                self.store.set_status(stored.run_id, TERMINAL[ended.event_type], error=error, finished_at=time.time())
                continue
            attempt = self.store.bump_attempts(stored.run_id)
            if attempt > self.max_attempts:
                message = f"Stopped after {self.max_attempts} restarts while it was running."
                self.store.append_events(stored.run_id, len(events) + 1, [ControlPlaneEvent.typed("run_failed", {"message": message})])
                self.store.set_status(stored.run_id, "failed", error=message, finished_at=time.time())
                continue
            note = ControlPlaneEvent.typed(RUN_RESUMED, {"attempt": attempt})
            self.store.append_events(stored.run_id, len(events) + 1, [note])
            record = RunRecord(run_id=stored.run_id, context=stored.context, created_at=stored.created_at, events=[*events, note])
            task = stored.request.message[:TASK_REMINDER_MAX]
            request = stored.request.model_copy(update={"message": self.resume_message.format(task=task), "conversation": None})
            try:
                resumed.append(await self._schedule(record, RunSubmission(context=stored.context, request=request)))
            except RunCapacityError:
                self.store.set_status(stored.run_id, "failed", error="run capacity is full", finished_at=time.time())
        return resumed

    async def _schedule(self, record: RunRecord, submission: RunSubmission) -> RunRecord:
        persisted = len(record.events)

        def persist(event_type: str, payload: dict[str, Any]) -> None:
            # Runs after the manager's sink appended the event; parallel tools can append several first.
            nonlocal persisted
            del event_type, payload
            if record.run_id in self._interrupted:
                return
            fresh = record.events[persisted:]
            self.store.append_events(record.run_id, persisted + 1, fresh)
            persisted += len(fresh)
            for event in fresh:
                if event.event_type in TERMINAL:
                    # Now, with the event: a crash before the task's done callback must not resume a finished run.
                    status = TERMINAL[event.event_type]
                    error = str(event.payload.get("message") or "run failed") if status == "failed" else None
                    self.store.set_status(record.run_id, status, error=error, finished_at=time.time())

        async def execute(run_id: str, sink: EventSink) -> None:
            self.store.set_status(run_id, "running", started_at=time.time())
            sink.subscribe(persist)
            await self.executor.execute(run_id, submission, sink)

        await self.manager.adopt(record, execute)
        assert record.task is not None
        record.task.add_done_callback(lambda _: self._finished(record, persist))
        return record

    def _finished(self, record: RunRecord, persist: Any) -> None:
        if record.run_id in self._interrupted:
            # Shutting down is not the user cancelling: leave it `running` for the next process to resume.
            return
        persist("", {})
        if any(event.event_type in TERMINAL for event in record.events):
            return  # `persist` wrote the status with the terminal event
        status: RunStatus = record.status if record.status in FINISHED else "failed"
        self.store.set_status(record.run_id, status, error=record.error, finished_at=record.finished_at or time.time())

    async def get(self, run_id: str) -> RunRecord:
        try:
            return await self.manager.get(run_id)
        except RunNotFoundError:
            stored = self.store.get(run_id)
            if stored is None:
                raise
            return RunRecord(
                run_id=run_id,
                context=stored.context,
                status=stored.status,
                created_at=stored.created_at,
                started_at=stored.started_at,
                finished_at=stored.finished_at,
                error=stored.error,
                events=self.store.events(run_id),
            )

    async def events(self, run_id: str, *, after: int = 0) -> AsyncIterator[tuple[int, ControlPlaneEvent]]:
        try:
            await self.manager.get(run_id)
        except RunNotFoundError:
            # Finished before this process started (or not resumed): replay what was stored.
            record = await self.get(run_id)
            for ordinal, event in enumerate(record.events[max(after, 0):], start=max(after, 0) + 1):
                yield ordinal, event
            return
        async for item in self.manager.events(run_id, after=after):
            yield item

    async def cancel(self, run_id: str) -> RunRecord:
        try:
            return await self.manager.cancel(run_id)
        except RunNotFoundError:
            record = await self.get(run_id)
            if record.status not in FINISHED:
                # Stored as unfinished but not running here: it will not be resumed.
                self.store.set_status(run_id, "cancelled", finished_at=time.time())
                record.status = "cancelled"
            return record

    async def shutdown(self) -> None:
        self._interrupted = self.manager.active()
        await self.manager.shutdown()


__all__ = [
    "DurableRunBackend",
    "RESUME_MESSAGE",
    "RUN_RESUMED",
    "SQLiteDatabase",
    "SQLitePersistence",
    "SQLiteRunStore",
    "StoredRun",
]

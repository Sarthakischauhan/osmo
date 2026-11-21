"""Durable agent runs: stored as they go, resumed (same run id, same session, events continued) after a restart."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from core_ai import ModelRegistry
from core_ai.providers.base import BaseProvider
from core_ai.types import Message, StreamEvent
from core_harness import Checkpoint, ControlPlaneEvent, EventSink
from core_server import RunContext, RunRequest, RunSubmission

from conftest import make_client, xai_key_store
from symphony_server.settings import Settings
from symphony_server.upstream.durable import RUN_RESUMED, DurableRunBackend, SQLiteDatabase, SQLitePersistence, SQLiteRunStore


class GatedExecutor:
    """Stands in for RunExecutor: emits a step, then finishes when `gate` opens (or hangs until cancelled)."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.seen: list[tuple[str, RunSubmission]] = []

    async def execute(self, run_id: str, submission: RunSubmission, sink: EventSink) -> None:
        self.seen.append((run_id, submission))
        await sink.emit("tool_execution_started", {"tool_name": "shell", "arguments": {"command": "make"}})
        self.started.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            # What the harness does when its task is cancelled.
            await sink.emit("run_cancelled", {})
            return
        await sink.emit("run_completed", {"output_text": f"done: {submission.request.message[:20]}"})


def submission(task: str = "open a PR") -> RunSubmission:
    return RunSubmission(RunContext(session_id="agent-1", metadata={"thread": "t1"}), RunRequest(message=task, session_id="agent-1"))


async def finished(backend: DurableRunBackend, run_id: str) -> str:
    events = [event async for _, event in backend.events(run_id)]
    return events[-1].event_type


async def test_a_finished_run_and_its_events_outlive_the_backend(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "runs.db")
    executor = GatedExecutor()
    backend = DurableRunBackend(executor, SQLiteRunStore(db))  # type: ignore[arg-type]
    run = await backend.submit(submission())
    executor.gate.set()
    assert await finished(backend, run.run_id) == "run_completed"
    # Closed before the run task's done callback: the status went in with the terminal event.
    db.close()
    await backend.shutdown()

    reopened = DurableRunBackend(GatedExecutor(), SQLiteRunStore(SQLiteDatabase(tmp_path / "runs.db")))  # type: ignore[arg-type]
    assert await reopened.recover() == []
    record = await reopened.get(run.run_id)
    assert record.status == "completed" and record.context.metadata == {"thread": "t1"}
    replayed = [(ordinal, event.event_type) async for ordinal, event in reopened.events(run.run_id, after=1)]
    assert replayed == [(2, "run_completed")]


async def test_a_run_stopped_by_shutdown_resumes_on_the_same_id_and_session(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "runs.db")
    first = GatedExecutor()
    backend = DurableRunBackend(first, SQLiteRunStore(db))  # type: ignore[arg-type]
    run = await backend.submit(submission("fix the flaky test and open a PR"))
    await first.started.wait()
    await backend.shutdown()
    # Shutting down is not cancelling: still `running`, and the teardown's run_cancelled was not recorded.
    store = SQLiteRunStore(db)
    stored = store.get(run.run_id)
    assert stored is not None and stored.status == "running"
    assert [event.event_type for event in store.events(run.run_id)] == ["tool_execution_started"]

    second = GatedExecutor()
    resumed_backend = DurableRunBackend(second, SQLiteRunStore(db))  # type: ignore[arg-type]
    [resumed] = await resumed_backend.recover()
    assert resumed.run_id == run.run_id
    await second.started.wait()
    [(run_id, again)] = second.seen
    assert run_id == run.run_id and again.context.session_id == "agent-1"
    assert again.request.message.startswith("[resumed]") and "fix the flaky test and open a PR" in again.request.message
    assert again.request.conversation is None  # the harness loads the stored conversation
    second.gate.set()
    events = [(ordinal, event.event_type) async for ordinal, event in resumed_backend.events(run.run_id)]
    assert events == [(1, "tool_execution_started"), (2, RUN_RESUMED), (3, "tool_execution_started"), (4, "run_completed")]
    await asyncio.sleep(0)
    assert SQLiteRunStore(db).get(run.run_id).status == "completed"  # type: ignore[union-attr]


async def test_a_run_that_keeps_dying_is_given_up(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "runs.db")
    executor = GatedExecutor()
    backend = DurableRunBackend(executor, SQLiteRunStore(db), max_attempts=1)  # type: ignore[arg-type]
    run = await backend.submit(submission())
    await executor.started.wait()
    await backend.shutdown()
    again = DurableRunBackend(GatedExecutor(), SQLiteRunStore(db), max_attempts=1)  # type: ignore[arg-type]
    assert len(await again.recover()) == 1
    await again.shutdown()
    last = DurableRunBackend(GatedExecutor(), SQLiteRunStore(db), max_attempts=1)  # type: ignore[arg-type]
    assert await last.recover() == []
    record = await last.get(run.run_id)
    assert record.status == "failed" and "restarts" in (record.error or "")
    assert record.events[-1].event_type == "run_failed"


async def test_a_cancelled_run_is_not_resumed(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "runs.db")
    executor = GatedExecutor()
    backend = DurableRunBackend(executor, SQLiteRunStore(db))  # type: ignore[arg-type]
    run = await backend.submit(submission())
    await executor.started.wait()
    await backend.cancel(run.run_id)
    await asyncio.sleep(0)
    assert SQLiteRunStore(db).get(run.run_id).status == "cancelled"  # type: ignore[union-attr]
    assert await DurableRunBackend(GatedExecutor(), SQLiteRunStore(db)).recover() == []  # type: ignore[arg-type]


async def test_a_run_whose_terminal_event_was_stored_is_not_run_again(tmp_path: Path) -> None:
    store = SQLiteRunStore(SQLiteDatabase(tmp_path / "runs.db"))
    store.insert("r1", submission(), time.time())
    store.set_status("r1", "running")
    # The process died after storing the event and before anything else.
    store.append_events("r1", 1, [ControlPlaneEvent.typed("run_failed", {"message": "model refused"})])
    executor = GatedExecutor()
    assert await DurableRunBackend(executor, store).recover() == []  # type: ignore[arg-type]
    assert executor.seen == []
    stored = store.get("r1")
    assert stored is not None and (stored.status, stored.error) == ("failed", "model refused")


async def test_sqlite_persistence_round_trips_conversations_and_checkpoints(tmp_path: Path) -> None:
    persistence = SQLitePersistence(SQLiteDatabase(tmp_path / "runs.db"))
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="", tool_calls=[{"id": 7, "type": "function", "function": {"name": "shell", "arguments": "{}"}}]),
        Message(role="tool", content="ok", tool_call_id="7"),
    ]
    await persistence.save_conversation(session_id="s", messages=messages)
    await persistence.save_checkpoint(checkpoint=Checkpoint(session_id="s", turn=2, messages=messages))
    reopened = SQLitePersistence(SQLiteDatabase(tmp_path / "runs.db"))
    assert await reopened.load_conversation(session_id="s") == messages
    checkpoint = await reopened.load_checkpoint(session_id="s")
    assert checkpoint is not None and checkpoint.turn == 2
    assert reopened.prune(time.time() + 1) == 1
    assert await reopened.load_conversation(session_id="s") == []


# ------------------------------------------------------------ through the Osmo app, across a restart


class HangingAgent(BaseProvider):
    """The agent model of the first server: never answers (the server stops while it thinks)."""

    def __init__(self) -> None:
        self.asked = threading.Event()

    async def stream(self, model_name: str, messages: list[Message], tools: list[dict[str, Any]] = [], **_: Any) -> AsyncGenerator[StreamEvent, None]:  # noqa: B006
        self.asked.set()
        await asyncio.Event().wait()
        yield StreamEvent(type="done")


class RecordingAgent(BaseProvider):
    """The agent model of the second server: answers at once and keeps what it was sent."""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def stream(self, model_name: str, messages: list[Message], tools: list[dict[str, Any]] = [], **_: Any) -> AsyncGenerator[StreamEvent, None]:  # noqa: B006
        self.messages = list(messages)
        yield StreamEvent(type="text_delta", delta="Done after the restart.")
        yield StreamEvent(type="done")


def server(tmp_path: Path, agent: BaseProvider) -> Any:
    registry = ModelRegistry()
    settings = Settings(xai_base_url="http://127.0.0.1:9/v1", database_file=tmp_path / "osmo.db", tool_categories=("search",))
    client = make_client(settings, secret_store=xai_key_store(), registry=registry)
    registry.register("grok", agent)
    return client


def test_an_agent_run_survives_an_osmo_restart(tmp_path: Path) -> None:
    hanging = HangingAgent()
    with server(tmp_path, hanging) as first:
        thread = first.post("/api/threads", json={}).json()
        run = first.post("/api/runs/start/task", json={"threadId": thread["id"], "task": "Summarize the release notes"}).json()
        assert hanging.asked.wait(10)
    # The server stopped mid-run. The next one picks the run up where it was.
    recording = RecordingAgent()
    with server(tmp_path, recording) as second:
        deadline = time.monotonic() + 10
        listed: dict[str, Any] = {}
        while time.monotonic() < deadline:
            listed = {item["runId"]: item for item in second.get("/api/runs").json()["runs"]}
            if listed.get(run["runId"], {}).get("status") == "completed":
                break
            time.sleep(0.05)
        assert listed[run["runId"]]["status"] == "completed"
        assert listed[run["runId"]]["result"] == "Done after the restart."
    users = [str(message.content) for message in recording.messages if message.role == "user"]
    # The stored conversation came back (the original task), followed by the resume note.
    assert users[0] == "Summarize the release notes"
    assert users[-1].startswith("[resumed]")


@pytest.fixture(autouse=True)
def _quiet_workspaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))

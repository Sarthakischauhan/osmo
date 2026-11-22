"""Agent notification delivery is best-effort and cannot fail a durable run."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from symphony_server.agents import AgentRuns, Notice
from symphony_server.protocol import AgentUpdate


class FailingObserver:
    async def show(self, event: AgentUpdate) -> None:
        raise RuntimeError("UI socket disconnected")

    async def tell(self, notice: Notice) -> None:
        raise RuntimeError("Voice bridge disconnected")


class RecordingStorage:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def add_message(self, thread_id: str, role: str, text: str) -> None:
        self.messages.append((thread_id, role, text))


@pytest.mark.asyncio
async def test_broken_ui_observer_cannot_fail_run_delivery() -> None:
    observer = FailingObserver()
    runtime = SimpleNamespace(observers={"t1": observer})
    await AgentRuns.show(runtime, "t1", AgentUpdate(runId="r1", task="test", status="running"))
    assert runtime.observers == {}


@pytest.mark.asyncio
async def test_notice_is_persisted_even_when_voice_observer_throws() -> None:
    storage = RecordingStorage()
    observer = FailingObserver()
    runtime = SimpleNamespace(observers={"t1": observer}, storage=storage)
    record = SimpleNamespace(threadId="t1", runId="r1")
    await AgentRuns.notify(runtime, record, Notice("r1", "interrupt", "[agent r1] done: test"))
    assert storage.messages == [("t1", "agent", "[agent r1] done: test")]
    assert runtime.observers == {}


@pytest.mark.asyncio
async def test_notice_survives_without_active_voice_session() -> None:
    storage = RecordingStorage()
    runtime = SimpleNamespace(observers={}, storage=storage)
    record = SimpleNamespace(threadId="t1", runId="r1")
    await AgentRuns.notify(runtime, record, Notice("r1", "silent", "[agent r1] was cancelled"))
    assert storage.messages == [("t1", "agent", "[agent r1] was cancelled")]

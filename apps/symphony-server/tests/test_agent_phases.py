"""An agent run's phase goes to the widget and to agent_status, never to the voice model."""

from __future__ import annotations

import queue
from typing import Any, cast

from conftest import FakeGrok
from core_harness import ControlPlaneEvent
from test_agents import Step, agent_client, start_agent
from test_browser import HoldingAgent
from test_session import call, next_event, say, tool_output

from symphony_server.agents import AgentRuns, Notice
from symphony_server.browser import BrowserSessions
from symphony_server.canvas import WidgetBoard
from symphony_server.protocol import AgentUpdate, ServerEvent
from symphony_server.storage import AgentRunRecord, SQLiteStorage


class Observer:
    """A thread's observer that records what a run shows and tells it."""

    def __init__(self) -> None:
        self.shown: list[ServerEvent] = []
        self.told: list[Notice] = []

    async def show(self, event: ServerEvent) -> None:
        self.shown.append(event)

    async def tell(self, notice: Notice) -> None:
        self.told.append(notice)


def runs_with_observer() -> tuple[AgentRuns, Observer]:
    # on_event touches only storage, browsers, the maps and the observer.
    runs = AgentRuns(cast(Any, None), SQLiteStorage(None), cast(Any, None), cast(Any, None), "grok:test", BrowserSessions(None, WidgetBoard()))
    record = AgentRunRecord(runId="r1", threadId="t1", task="x", status="running", createdAt=1, updatedAt=1)
    runs.runs["r1"] = record
    runs.storage.save_run(record)
    observer = Observer()
    runs.observe("t1", observer)
    return runs, observer


async def test_a_tool_start_updates_the_widget_and_tells_the_voice_model_nothing() -> None:
    runs, observer = runs_with_observer()
    for query in ("a cat", "a dog"):
        await runs.on_event("r1", ControlPlaneEvent.typed("tool_execution_started", {"tool_name": "web_search", "arguments": {"query": query}}))
    assert observer.told == []
    assert [(e.status, e.phase) for e in observer.shown if isinstance(e, AgentUpdate)] == [
        ("running", "Searching the web · a cat"),
        ("running", "Searching the web · a dog"),
    ]
    assert runs.phases == {"r1": "Searching the web · a dog"}


async def test_done_and_failed_still_interrupt_and_cancelled_and_approvals_stay_silent() -> None:
    runs, observer = runs_with_observer()
    await runs.on_event("r1", ControlPlaneEvent.typed("tool_execution_started", {"tool_name": "web_search", "arguments": {"query": "x"}}))
    await runs.on_event("r1", ControlPlaneEvent.typed("run_completed", {"output_text": "The answer."}))
    assert [(n.tag, n.text) for n in observer.told] == [("interrupt", "[agent r1] done: The answer.")]
    assert runs.phases == {}

    runs, observer = runs_with_observer()
    await runs.on_event("r1", ControlPlaneEvent.typed("run_failed", {"message": "boom"}))
    assert [(n.tag, n.text) for n in observer.told] == [("interrupt", "[agent r1] failed: boom")]

    runs, observer = runs_with_observer()
    await runs.on_event("r1", ControlPlaneEvent.typed("run_cancelled"))
    assert [(n.tag, n.text) for n in observer.told] == [("silent", "[agent r1] was cancelled")]


def items_until(grok: FakeGrok, prefix: str) -> list[str]:
    """Every message the bridge adds to the voice conversation, up to the first that starts with `prefix`."""
    texts: list[str] = []
    while True:
        item = grok.next_of("conversation.item.create")["item"]
        text = item["content"][0]["text"] if item["type"] == "message" else item["type"]
        texts.append(text)
        if text.startswith(prefix):
            return texts


def test_phases_never_reach_the_voice_model_and_agent_status_has_the_latest(grok: FakeGrok) -> None:
    agent = HoldingAgent([Step(tool="alpha"), Step(tool="beta"), Step(text="All done.")])
    agent.release.set()
    with agent_client(grok, agent) as client, client.websocket_connect("/ws") as ws:
        run_id = start_agent(ws, grok, "do two things")
        phases: list[str | None] = []
        while True:
            update = next_event(ws, "agent.update")
            phases.append(update.get("phase"))
            if update["status"] == "completed":
                break
        assert phases == ["Running alpha", "Running beta", None]
        # Nothing but the finished run's own notice was added to the voice conversation, and it asked for a response.
        assert items_until(grok, f"[agent {run_id}]") == [f"[agent {run_id}] done: All done."]
        grok.next_of("response.create")
        extra: list[str] = []
        try:
            while True:
                extra.append(grok.next_json(timeout=0.3)["type"])
        except queue.Empty:
            pass
        assert "conversation.item.create" not in extra


def test_agent_status_reports_the_phase_the_run_is_in(grok: FakeGrok) -> None:
    agent = HoldingAgent([Step(tool="alpha"), Step(text="All done.")])
    with agent_client(grok, agent) as client, client.websocket_connect("/ws") as ws:
        run_id = start_agent(ws, grok, "do one thing")
        assert next_event(ws, "agent.update")["phase"] == "Running alpha"
        say(grok, "How is it going?")
        call(grok, "agent_status", {"run_id": run_id}, "s1", "r2")
        status = tool_output(grok)
        assert (status["status"], status["phase"]) == ("running", "Running alpha")
        agent.release.set()
        while next_event(ws, "agent.update")["status"] != "completed":
            pass
        assert items_until(grok, f"[agent {run_id}]") == [f"[agent {run_id}] done: All done."]
        grok.next_of("response.create")
        call(grok, "agent_status", {"run_id": run_id}, "s2", "r3")
        finished = tool_output(grok)
        assert (finished["status"], finished["phase"], finished["result"]) == ("completed", None, "All done.")


def test_a_failed_run_interrupts_the_voice_model(grok: FakeGrok) -> None:
    agent = HoldingAgent([Step(tool="alpha")])  # no second step: the next model call raises
    with agent_client(grok, agent) as client, client.websocket_connect("/ws") as ws:
        run_id = start_agent(ws, grok, "do one thing")
        agent.release.set()
        texts = items_until(grok, f"[agent {run_id}] failed")
        assert len(texts) == 1
        grok.next_of("response.create")

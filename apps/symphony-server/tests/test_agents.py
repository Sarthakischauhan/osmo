"""run_agent: a background harness run the voice session hears about, with approvals in the web app."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from core_ai import ModelRegistry
from core_ai.providers.base import BaseProvider
from core_ai.types import Message, StreamEvent
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from conftest import FakeGrok, make_client, xai_key_store
from symphony_server.settings import Settings
from test_session import call, next_event, say, start, tool_output


@dataclass(frozen=True)
class Step:
    """One scripted agent model turn: a tool call, or a final text answer."""

    text: str = ""
    tool: str = ""
    arguments: str = "{}"


class ScriptedAgent(BaseProvider):
    """The agent's text model: plays `steps` in order, one per model call, each after `gate` opens."""

    def __init__(self, steps: list[Step]) -> None:
        self.steps = list(steps)
        self.gate = threading.Event()
        self.gate.set()

    async def stream(
        self,
        model_name: str,
        messages: list[Message],
        tools: list[dict[str, Any]] = [],  # noqa: B006 - BaseProvider's signature
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        await asyncio.to_thread(self.gate.wait, 10)
        step = self.steps.pop(0)
        if step.tool:
            yield StreamEvent(type="toolcall_start", tool_call_id=f"t-{len(self.steps)}", tool_name=step.tool)
            yield StreamEvent(type="toolcall_delta", delta=step.arguments)
        else:
            yield StreamEvent(type="text_delta", delta=step.text)
        yield StreamEvent(type="done")


def agent_client(grok: FakeGrok, agent: ScriptedAgent, approval_tools: tuple[str, ...] = ()) -> TestClient:
    registry = ModelRegistry()
    settings = Settings(xai_base_url=grok.base_url, approval_tools=approval_tools)
    client = make_client(settings, secret_store=xai_key_store(), registry=registry)
    # The agent model's namespace streams from the script instead of xAI.
    registry.register("grok", agent)
    return client


def done_after(client: TestClient, thread_runs: int) -> float:
    """A time just after the latest of the first thread's `thread_runs` runs last changed."""
    thread_id = client.get("/api/threads").json()["threads"][0]["id"]
    runs = client.get(f"/api/threads/{thread_id}").json()["runs"]
    assert len(runs) == thread_runs
    return float(max(run["updatedAt"] for run in runs)) + 0.001


def told(grok: FakeGrok, prefix: str) -> str:
    """The next message the bridge adds to the voice conversation that starts with `prefix`."""
    while True:
        item = grok.next_of("conversation.item.create")["item"]
        if item["type"] == "message" and item["content"][0]["text"].startswith(prefix):
            text: str = item["content"][0]["text"]
            return text


def start_agent(ws: WebSocketTestSession, grok: FakeGrok, task: str) -> str:
    start(ws, grok)
    say(grok, "Can you handle this?")
    call(grok, "run_agent", {"task": task}, "a1")
    started = tool_output(grok)
    assert started["status"] == "running"
    run_id: str = started["run_id"]
    assert next_event(ws, "agent.update") == {"type": "agent.update", "runId": run_id, "task": task, "status": "running"}
    return run_id


@pytest.fixture
def denied_search() -> ScriptedAgent:
    return ScriptedAgent([Step(tool="web_search", arguments=json.dumps({"query": "a cat"})), Step(text="Skipped the search.")])


def test_an_agent_run_asks_for_approval_and_reports_back(grok: FakeGrok, denied_search: ScriptedAgent) -> None:
    with agent_client(grok, denied_search, approval_tools=("web_search",)) as client, client.websocket_connect("/ws") as ws:
        run_id = start_agent(ws, grok, "look up a cat")

        assert next_event(ws, "agent.update")["status"] == "waiting_approval"
        asked = next_event(ws, "approval.request")
        assert asked["runId"] == run_id and asked["tool"] == "web_search"
        # Needing approval interrupts: the voice model is told and asked to speak.
        assert "needs approval" in told(grok, f"[agent {run_id}]")
        grok.next_of("response.create")
        # The home screen lists the waiting run with what it asks for.
        listed = client.get("/api/runs").json()
        assert [(run["runId"], run["status"]) for run in listed["runs"]] == [(run_id, "waiting_approval")]
        assert [approval["approvalId"] for approval in listed["approvals"]] == [asked["approvalId"]]

        assert client.post(f"/api/approvals/{asked['approvalId']}", json={"approved": False}).status_code == 204
        assert next_event(ws, "approval.resolved") == {
            "type": "approval.resolved", "approvalId": asked["approvalId"], "runId": run_id, "approved": False,
        }
        assert told(grok, f"[agent {run_id}]") == f"[agent {run_id}] the user denied the step"

        done = next_event(ws, "agent.update")
        while done["status"] != "completed":
            done = next_event(ws, "agent.update")
        assert done["result"] == "Skipped the search."
        assert client.get("/api/runs").json()["approvals"] == []
        # Finished runs are listed only when asked for since before they finished.
        assert client.get("/api/runs", params={"since": done_after(client, thread_runs=1)}).json()["runs"] == []
        assert [run["status"] for run in client.get("/api/runs").json()["runs"]] == ["completed"]
        assert told(grok, f"[agent {run_id}]") == f"[agent {run_id}] done: Skipped the search."
        grok.next_of("response.create")

        thread_id = client.get("/api/threads").json()["threads"][0]["id"]
        detail = client.get(f"/api/threads/{thread_id}").json()
        assert [(run["runId"], run["status"]) for run in detail["runs"]] == [(run_id, "completed")]
        assert [m["role"] for m in detail["messages"]][:1] == ["user"]
        assert f"[agent {run_id}] done: Skipped the search." in [m["text"] for m in detail["messages"]]
        # An answered approval cannot be answered again.
        assert client.post(f"/api/approvals/{asked['approvalId']}", json={"approved": True}).status_code == 404


@pytest.fixture
def slow_answer() -> Iterator[ScriptedAgent]:
    agent = ScriptedAgent([Step(text="All done.")])
    agent.gate.clear()
    yield agent
    agent.gate.set()


def test_barge_in_stops_the_speech_but_not_the_agent(grok: FakeGrok, slow_answer: ScriptedAgent) -> None:
    with agent_client(grok, slow_answer) as client, client.websocket_connect("/ws") as ws:
        run_id = start_agent(ws, grok, "research it")
        grok.send({"type": "input_audio_buffer.speech_started"})
        while next_event(ws, "state").get("detail") != "barge-in":
            pass

        say(grok, "Is it still going?")
        call(grok, "agent_status", {"run_id": run_id}, "s1", "r2")
        assert tool_output(grok)["status"] == "running"

        slow_answer.gate.set()
        assert told(grok, f"[agent {run_id}]") == f"[agent {run_id}] done: All done."
        thread_id = client.get("/api/threads").json()["threads"][0]["id"]
        assert client.get(f"/api/threads/{thread_id}").json()["runs"][0]["status"] == "completed"


def test_cancel_agent_stops_the_run(grok: FakeGrok, slow_answer: ScriptedAgent) -> None:
    with agent_client(grok, slow_answer) as client, client.websocket_connect("/ws") as ws:
        run_id = start_agent(ws, grok, "research it")
        say(grok, "Never mind, stop it.")
        call(grok, "cancel_agent", {"run_id": run_id}, "c1", "r2")
        tool_output(grok)
        update = next_event(ws, "agent.update")
        while update["status"] != "cancelled":
            update = next_event(ws, "agent.update")
        assert told(grok, f"[agent {run_id}]") == f"[agent {run_id}] was cancelled"
        slow_answer.gate.set()  # let the abandoned model call return before the server shuts down


def test_agent_tools_only_reach_runs_of_the_open_thread(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "status?")
        call(grok, "agent_status", {"run_id": "run-elsewhere"}, "s1")
        assert tool_output(grok) == {"error": "no such run in this conversation"}

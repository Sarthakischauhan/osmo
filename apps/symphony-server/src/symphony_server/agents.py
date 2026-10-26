"""Background agent runs: the voice model hands a task to a harness run and keeps talking.

`run_agent` submits a core_server run (on a text model, through the server's run backend) and
answers at once with its run id. `AgentRuns` follows each run's events, keeps the run's row in
storage, and tells the thread's observer (the open voice bridge, if any) what happened:
wire events for the browser, and `Notice`s for the voice model, each tagged with when it
should be spoken: `interrupt` (failed, needs approval, done), `when_idle` (a new phase) or
`silent` (added to the conversation without a response).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from core_harness import ControlPlaneEvent, EventSink, Tool
from core_server import InProcessRunBackend, RunCapacityError, RunContext, RunRequest, RunSubmission
from pydantic import BaseModel

from symphony_server.approvals import (
    APPROVAL_REQUIRED,
    APPROVAL_RESOLVED,
    ApprovalBroker,
    ApprovalPayload,
    ApprovalResolvedPayload,
)
from symphony_server.canvas import OSMO_WIDGET
from symphony_server.model_access import ModelAccess
from symphony_server.protocol import (
    AgentUpdate,
    ApprovalRequest,
    ApprovalResolved,
    ServerEvent,
    widget_event_adapter,
)
from symphony_server.providers import provider_label
from symphony_server.storage import AgentRunRecord, AgentRunStatus, Storage
from symphony_server.turn_events import RunFailedPayload, ToolStartedPayload, activity_label
from symphony_server.voices import provider_of

log = logging.getLogger("symphony.agents")

NoticeTag = Literal["interrupt", "when_idle", "silent"]
# A finished run's answer, as told to the voice model (it summarizes it out loud).
RESULT_TOLD_MAX = 4000
TASK_MAX = 4000

RUN_AGENT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"task": {"type": "string", "description": "What the agent should do, in full, self-contained."}},
    "required": ["task"],
}
RUN_ID_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"run_id": {"type": "string", "description": "The run id run_agent returned."}},
    "required": ["run_id"],
}


@dataclass(frozen=True)
class Notice:
    """Something the voice model should hear about a run, and when."""

    run_id: str
    tag: NoticeTag
    text: str


class AgentObserver(Protocol):
    """Whoever has a thread open: shows wire events and relays notices to its voice model."""

    async def show(self, event: ServerEvent) -> None: ...

    async def tell(self, notice: Notice) -> None: ...


class CompletedPayload(BaseModel):
    output_text: str = ""


class AgentRuns:
    """Every agent run of this server, by run id; observers by thread id."""

    def __init__(self, backend: InProcessRunBackend, storage: Storage, broker: ApprovalBroker, access: ModelAccess, model_id: str) -> None:
        self.backend, self.storage, self.broker, self.access, self.model_id = backend, storage, broker, access, model_id
        self.runs: dict[str, AgentRunRecord] = {}
        self.sessions: dict[str, str] = {}
        self.observers: dict[str, AgentObserver] = {}
        self.tasks: set[asyncio.Task[None]] = set()

    def observe(self, thread_id: str, observer: AgentObserver) -> None:
        self.observers[thread_id] = observer

    def unobserve(self, thread_id: str, observer: AgentObserver) -> None:
        if self.observers.get(thread_id) is observer:
            del self.observers[thread_id]

    async def start(self, thread_id: str, task: str) -> AgentRunRecord | str:
        """Submit `task` as a run for `thread_id`; the record, or why it could not start."""
        provider = provider_of(self.model_id)
        if provider is None or not await self.access.prepare(provider):
            label = provider_label(provider) if provider is not None else self.model_id
            return f"the agent model ({self.model_id}) needs {label} connected"
        session_id = f"agent-{secrets.token_hex(8)}"
        request = RunRequest(message=task[:TASK_MAX], session_id=session_id, model_id=self.model_id)
        try:
            state = await self.backend.submit(RunSubmission(RunContext(session_id=session_id), request))
        except RunCapacityError as exc:
            return f"the server is busy: {exc}"
        now = time.time()
        record = AgentRunRecord(runId=state.run_id, threadId=thread_id, task=task, status="running", createdAt=now, updatedAt=now)
        self.runs[record.runId] = record
        self.sessions[record.runId] = session_id
        self.storage.save_run(record)
        self.spawn(self.follow(record.runId))
        return record

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def get(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id) or self.storage.get_run(run_id)

    async def cancel(self, run_id: str) -> AgentRunRecord | None:
        record = self.runs.get(run_id)
        if record is None:
            return self.storage.get_run(run_id)
        if record.status in ("running", "waiting_approval"):
            self.broker.deny_session(self.sessions[run_id])
            await self.backend.cancel(run_id)
        return record

    async def shutdown(self) -> None:
        for task in tuple(self.tasks):
            task.cancel()

    # ------------------------------------------------------------ run events

    async def follow(self, run_id: str) -> None:
        async for _, event in self.backend.events(run_id):
            await self.on_event(run_id, event)

    async def on_event(self, run_id: str, event: ControlPlaneEvent) -> None:
        record = self.runs[run_id]
        kind = event.event_type
        if kind == "tool_execution_started":
            started = ToolStartedPayload.model_validate(event.payload)
            phase = activity_label(started.tool_name, started.arguments)
            await self.update(record, "running", phase=phase)
            await self.notify(record, Notice(run_id, "when_idle", f"[agent {run_id}] now: {phase}"))
        elif kind == OSMO_WIDGET:
            await self.show(record, widget_event_adapter.validate_python(event.payload))
        elif kind == APPROVAL_REQUIRED:
            asked = ApprovalPayload.model_validate(event.payload)
            await self.update(record, "waiting_approval", phase=asked.summary)
            await self.show(
                record,
                ApprovalRequest(approvalId=asked.approval_id, runId=run_id, tool=asked.tool, summary=asked.summary, arguments=asked.arguments),
            )
            text = f"[agent {run_id}] needs approval to: {asked.summary}. Ask the user to tap Approve or Deny on screen."
            await self.notify(record, Notice(run_id, "interrupt", text))
        elif kind == APPROVAL_RESOLVED:
            resolved = ApprovalResolvedPayload.model_validate(event.payload)
            await self.update(record, "running")
            await self.show(record, ApprovalResolved(approvalId=resolved.approval_id, runId=run_id, approved=resolved.approved))
            verdict = "approved" if resolved.approved else "denied"
            await self.notify(record, Notice(run_id, "silent", f"[agent {run_id}] the user {verdict} the step"))
        elif kind == "run_completed":
            result = CompletedPayload.model_validate(event.payload).output_text
            await self.update(record, "completed", result=result)
            await self.notify(record, Notice(run_id, "interrupt", f"[agent {run_id}] done: {result[:RESULT_TOLD_MAX]}"))
        elif kind in ("run_failed", "run_limit_exceeded"):
            message = RunFailedPayload.model_validate(event.payload).message
            await self.update(record, "failed", result=message)
            await self.notify(record, Notice(run_id, "interrupt", f"[agent {run_id}] failed: {message}"))
        elif kind == "run_cancelled":
            await self.update(record, "cancelled")
            await self.notify(record, Notice(run_id, "silent", f"[agent {run_id}] was cancelled"))

    async def update(self, record: AgentRunRecord, status: AgentRunStatus, *, phase: str | None = None, result: str | None = None) -> None:
        changed = record.model_copy(update={"status": status, "result": result or record.result, "updatedAt": time.time()})
        self.runs[record.runId] = changed
        self.storage.save_run(changed)
        await self.show(changed, AgentUpdate(runId=changed.runId, task=changed.task, status=status, phase=phase, result=changed.result))

    async def show(self, record: AgentRunRecord, event: ServerEvent) -> None:
        observer = self.observers.get(record.threadId)
        if observer is not None:
            await observer.show(event)

    async def notify(self, record: AgentRunRecord, notice: Notice) -> None:
        observer = self.observers.get(record.threadId)
        if observer is not None:
            await observer.tell(notice)


@dataclass
class AgentDesk:
    """The agent runs one voice session can reach: those of the thread it has open."""

    runs: AgentRuns
    thread_id: str = ""

    def mine(self, run_id: str) -> AgentRunRecord | None:
        record = self.runs.get(run_id)
        return record if record is not None and record.threadId == self.thread_id else None


def run_status(record: AgentRunRecord) -> str:
    return json.dumps({"run_id": record.runId, "status": record.status, "task": record.task, "result": record.result})


class RunAgentTool(Tool):
    def __init__(self, desk: AgentDesk) -> None:
        super().__init__(
            name="run_agent",
            description=(
                "Start a background agent for multi-step work (research, comparisons, drafting, pictures). "
                "Returns a run_id at once; keep talking. You will be told when it needs approval, fails or is done."
            ),
            parameters=RUN_AGENT_PARAMETERS,
        )
        self.desk = desk

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        del sink
        task = str(args.get("task", "")).strip()
        if not task:
            return json.dumps({"error": "task must say what to do"})
        started = await self.desk.runs.start(self.desk.thread_id, task)
        if isinstance(started, str):
            return json.dumps({"error": started})
        return json.dumps({"run_id": started.runId, "status": started.status, "say": "Say in one line that you started on it."})


class AgentStatusTool(Tool):
    def __init__(self, desk: AgentDesk) -> None:
        super().__init__(name="agent_status", description="The status (and result, once done) of a run_agent run.", parameters=RUN_ID_PARAMETERS)
        self.desk = desk

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        del sink
        record = self.desk.mine(str(args.get("run_id", "")))
        return run_status(record) if record is not None else json.dumps({"error": "no such run in this conversation"})


class CancelAgentTool(Tool):
    def __init__(self, desk: AgentDesk) -> None:
        super().__init__(name="cancel_agent", description="Stop a run_agent run the user no longer wants.", parameters=RUN_ID_PARAMETERS)
        self.desk = desk

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        del sink
        run_id = str(args.get("run_id", ""))
        if self.desk.mine(run_id) is None:
            return json.dumps({"error": "no such run in this conversation"})
        record = await self.desk.runs.cancel(run_id)
        return run_status(record) if record is not None else json.dumps({"error": "no such run"})

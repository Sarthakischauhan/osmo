"""The voice model's quick tools: running a call (behind the same approval gate an agent run has) and the sink the
tools emit widgets, code, voice changes and music commands through."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from core_ai.voice import ToolCall
from core_harness import EventSink, Tool

from symphony_server import tracing
from symphony_server.approvals import APPROVAL_REQUIRED, ApprovalAddon, ApprovalBroker, ApprovalPayload, ApprovalResolvedPayload
from symphony_server.canvas import OSMO_CODE, OSMO_WIDGET
from symphony_server.desktop import RUN_DESKTOP, DesktopSession
from symphony_server.music import OSMO_MUSIC
from symphony_server.protocol import ApprovalRequest, ApprovalResolved, MusicCommand, OsmoState, ServerEvent, TranscriptDelta, widget_event_adapter
from symphony_server.turn_events import activity_label
from symphony_server.voice_transcript import Transcript
from symphony_server.voices import OSMO_VOICE, Selection, SetVoiceArgs

log = logging.getLogger("symphony.voice")

# The run id a voice tool's approval card carries: it belongs to the voice session, not to an agent run.
VOICE_RUN = "voice"
# A voice tool waits this long for the user's Approve / Deny; the voice turn is held meanwhile, so it is shorter than a run's.
VOICE_APPROVAL_TIMEOUT_S = 120.0
# What a tool's error and output keep in its trace.
TRACE_OUTPUT_MAX = 4000

Emit = Callable[[ServerEvent], Awaitable[None]]
SetState = Callable[[OsmoState, str | None], Awaitable[None]]
Configure = Callable[[SetVoiceArgs, Literal["client", "tool"]], Awaitable[Selection | str]]


class VoiceToolSink(EventSink):
    """The sink a voice tool runs with: widget and voice events go straight to the browser."""

    def __init__(self, emit: Emit, transcript: Transcript, configure: Configure) -> None:
        super().__init__()
        self.show, self.transcript, self.configure = emit, transcript, configure

    async def deliver(self, event_type: str, payload: dict[str, Any]) -> None:
        """Route one tool event to the browser, the transcript or the voice settings."""
        if event_type == OSMO_WIDGET:
            await self.show(widget_event_adapter.validate_python(payload))
        elif event_type == OSMO_CODE:
            markdown = str(payload.get("markdown") or "").strip()
            if markdown:
                await self.show(TranscriptDelta(role="assistant", text=f"\n\n{markdown}\n"))
                self.transcript.hold(frozenset(self.transcript.unheard), markdown)
        elif event_type == OSMO_VOICE:
            await self.configure(SetVoiceArgs.model_validate(payload), "tool")
        elif event_type == OSMO_MUSIC:
            await self.show(MusicCommand.model_validate(payload))


def tool_output(result: Any) -> str:
    """A tool's result as the text the voice model gets back."""
    return result if isinstance(result, str) else json.dumps(result, default=str)


class VoiceToolCalls:
    """Runs the voice model's tool calls. The risky ones (vault_write, ...) wait on the user exactly as an agent run's do."""

    def __init__(
        self,
        tools: Sequence[Tool],
        *,
        broker: ApprovalBroker,
        approval_tools: frozenset[str],
        session_id: str,
        desktop: DesktopSession,
        sink: EventSink,
        emit: Emit,
        set_state: SetState,
    ) -> None:
        self.tools = {tool.name: tool for tool in tools}
        self.approvals = ApprovalAddon(broker, session_id, approval_tools, VOICE_APPROVAL_TIMEOUT_S)
        self.desktop, self.sink, self.emit, self.set_state = desktop, sink, emit, set_state

    async def run(self, call: ToolCall) -> str:
        """Run one call and return its result (or `{"error": ...}`) for the model."""
        log.info("voice requested tool %s", call.name)
        tool = self.tools.get(call.name)
        if tool is None:
            return json.dumps({"error": f"unknown tool {call.name}"})
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            return json.dumps({"error": "arguments must be a JSON object"})
        if not isinstance(args, dict):
            return json.dumps({"error": "arguments must be a JSON object"})
        await self.set_state("working", activity_label(call.name, args))
        refused = await self.approvals.before_tool(tool_name=call.name, arguments=args, emit=self.approval_event)
        if refused is not None:
            return json.dumps({"error": refused})
        token = RUN_DESKTOP.set(self.desktop)
        try:
            with tracing.tool_call(call.name, args) as observation:
                try:
                    result = tool_output(await tool.execute(sink=self.sink, args=args))
                except Exception as exc:  # a tool failure is the model's to explain, as in a harness run
                    log.warning("voice tool %s failed: %s", call.name, exc)
                    result = json.dumps({"error": str(exc) or type(exc).__name__})
                    if observation is not None:
                        observation.update(level="ERROR", status_message=type(exc).__name__)
                if observation is not None:
                    observation.update(output=result[:TRACE_OUTPUT_MAX])
                return result
        finally:
            RUN_DESKTOP.reset(token)

    async def approval_event(self, kind: str, payload: dict[str, Any]) -> None:
        """A voice tool's approval, as the same feed card an agent run's approval gets."""
        if kind == APPROVAL_REQUIRED:
            asked = ApprovalPayload.model_validate(payload)
            await self.emit(ApprovalRequest(approvalId=asked.approval_id, runId=VOICE_RUN, tool=asked.tool, summary=asked.summary, arguments=asked.arguments))
        else:
            resolved = ApprovalResolvedPayload.model_validate(payload)
            await self.emit(ApprovalResolved(approvalId=resolved.approval_id, runId=VOICE_RUN, approved=resolved.approved))

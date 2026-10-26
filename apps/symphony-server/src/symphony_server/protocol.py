"""Osmo wire protocol (browser <-> Symphony), mirroring packages/osmo-composer/src/protocol.ts.

Binary frames are PCM16 audio; text frames are the JSON events below.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from symphony_server.storage import AgentRunStatus, ThreadRecord
from symphony_server.voices import Effort, Selection, VoiceModel

OsmoState = Literal["idle", "listening", "thinking", "speaking", "working"]


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionStart(_Event):
    type: Literal["session.start"]
    voice: str | None = None
    model: str | None = None
    effort: Effort | None = None
    # Continue a stored thread (its messages are replayed into the voice session); a new one when unset.
    threadId: str | None = None


class SessionConfigure(_Event):
    """The picker's commit: switch model, voice and reasoning effort."""

    type: Literal["session.configure"]
    model: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    effort: Effort | None = None


class VoicePreviewRequest(_Event):
    type: Literal["voice.preview"]
    requestId: str = Field(min_length=1)
    model: str = Field(min_length=1)
    voice: str = Field(min_length=1)


class SessionEnd(_Event):
    type: Literal["session.end"]


class Interrupt(_Event):
    type: Literal["interrupt"]


class WidgetAction(_Event):
    type: Literal["widget.action"]
    id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    value: str | None = None


class DesktopConfigure(_Event):
    type: Literal["desktop.configure"]
    enabled: bool


class DesktopResult(_Event):
    type: Literal["desktop.result"]
    requestId: str = Field(min_length=1, max_length=64)
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=2000)


class DesktopRequest(_Event):
    type: Literal["desktop.request"] = "desktop.request"
    requestId: str
    action: dict[str, Any]


ClientEvent = Annotated[
    Union[SessionStart, SessionEnd, Interrupt, WidgetAction, SessionConfigure, VoicePreviewRequest, DesktopConfigure, DesktopResult],
    Field(discriminator="type"),
]
client_event_adapter: TypeAdapter[ClientEvent] = TypeAdapter(ClientEvent)


class StateEvent(_Event):
    type: Literal["state"] = "state"
    state: OsmoState
    detail: str | None = None


class TranscriptDelta(_Event):
    type: Literal["transcript.delta"] = "transcript.delta"
    role: Literal["user", "assistant"]
    text: str
    itemId: str | None = None


class RenderedWidget(_Event):
    id: str
    type: str
    props: dict[str, Any]


class WidgetRender(_Event):
    """Spec payload (id, type, props), nested because `type` is the discriminator."""

    type: Literal["widget.render"] = "widget.render"
    widget: RenderedWidget


class WidgetUpdate(_Event):
    type: Literal["widget.update"] = "widget.update"
    id: str
    patch: dict[str, Any]


class ModelsEvent(_Event):
    """The voice model registry the picker shows, plus the server's current selection."""

    type: Literal["models"] = "models"
    models: list[VoiceModel]
    selection: Selection


class VoiceConfigured(_Event):
    """The session's model/voice/effort changed (picker commit or the `set_voice` tool)."""

    type: Literal["voice.configured"] = "voice.configured"
    selection: Selection
    source: Literal["client", "tool"]
    # Set when the change was rejected or failed; `selection` is then what is still in effect.
    error: str | None = None


class VoicePreviewResult(_Event):
    """Answer to a `voice.preview` request: base64 PCM16 24 kHz mono, about 2 s, or an error."""

    type: Literal["voice.preview"] = "voice.preview"
    requestId: str
    model: str
    voice: str
    audio: str | None = None
    error: str | None = None


WidgetEvent = Annotated[Union[WidgetRender, WidgetUpdate], Field(discriminator="type")]
# The payload of an `osmo.widget` run event (see canvas.py).
widget_event_adapter: TypeAdapter[WidgetEvent] = TypeAdapter(WidgetEvent)

class ThreadEvent(_Event):
    """The stored thread this session reads and writes (sent on start and when its title changes)."""

    type: Literal["thread"] = "thread"
    thread: ThreadRecord


class AgentUpdate(_Event):
    """A background agent run started by `run_agent` changed status or phase."""

    type: Literal["agent.update"] = "agent.update"
    runId: str
    task: str
    status: AgentRunStatus
    phase: str | None = None
    result: str | None = None


class ApprovalRequest(_Event):
    """An agent run waits for the user to approve a risky step (`POST /api/approvals/{approvalId}`)."""

    type: Literal["approval.request"] = "approval.request"
    approvalId: str
    runId: str
    tool: str
    summary: str
    arguments: str


class ApprovalResolved(_Event):
    type: Literal["approval.resolved"] = "approval.resolved"
    approvalId: str
    runId: str
    approved: bool


ServerEvent = Union[
    StateEvent,
    TranscriptDelta,
    WidgetRender,
    WidgetUpdate,
    ModelsEvent,
    VoiceConfigured,
    VoicePreviewResult,
    ThreadEvent,
    AgentUpdate,
    ApprovalRequest,
    ApprovalResolved,
    DesktopRequest,
]

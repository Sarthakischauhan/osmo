"""Osmo wire protocol (browser <-> Symphony), mirroring packages/osmo-composer/src/protocol.ts.

Binary frames are PCM16 audio; text frames are the JSON events below.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from symphony_server.voices import Effort, Selection, VoiceModel

OsmoState = Literal["idle", "listening", "thinking", "speaking", "working"]


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionStart(_Event):
    type: Literal["session.start"]
    voice: str | None = None
    instructions: str | None = None
    model: str | None = None
    effort: Effort | None = None


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


ClientEvent = Annotated[
    Union[SessionStart, SessionEnd, Interrupt, WidgetAction, SessionConfigure, VoicePreviewRequest],
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


class VoicePreviewResult(_Event):
    """Answer to a `voice.preview` request: base64 PCM16 24 kHz mono, about 2 s, or an error."""

    type: Literal["voice.preview"] = "voice.preview"
    requestId: str
    model: str
    voice: str
    audio: str | None = None
    error: str | None = None


ServerEvent = Union[
    StateEvent, TranscriptDelta, WidgetRender, WidgetUpdate, ModelsEvent, VoiceConfigured, VoicePreviewResult
]

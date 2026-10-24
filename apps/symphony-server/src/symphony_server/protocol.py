"""Orb wire protocol (browser <-> Symphony), mirroring packages/orb-composer/src/protocol.ts.

Binary frames are PCM16 audio; text frames are the JSON events below.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

OrbState = Literal["idle", "listening", "thinking", "speaking", "working"]


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionStart(_Event):
    type: Literal["session.start"]
    voice: str | None = None
    instructions: str | None = None


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
    Union[SessionStart, SessionEnd, Interrupt, WidgetAction],
    Field(discriminator="type"),
]
client_event_adapter: TypeAdapter[ClientEvent] = TypeAdapter(ClientEvent)


class StateEvent(_Event):
    type: Literal["state"] = "state"
    state: OrbState
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


ServerEvent = Union[StateEvent, TranscriptDelta, WidgetRender, WidgetUpdate]

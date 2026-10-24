"""Typed subset of the xAI Voice Agent (realtime) events Symphony speaks.

Event names are taken from the official docs, not guessed:
- https://docs.x.ai/docs/guides/voice/agent
- https://docs.x.ai/developers/rest-api-reference/inference/voice
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# ---------------------------------------------------------------- server -> Symphony


class _Incoming(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SessionUpdated(_Incoming):
    type: Literal["session.updated"]


class SpeechStarted(_Incoming):
    type: Literal["input_audio_buffer.speech_started"]


class SpeechStopped(_Incoming):
    type: Literal["input_audio_buffer.speech_stopped"]


class InputTranscriptionCompleted(_Incoming):
    type: Literal["conversation.item.input_audio_transcription.completed"]
    item_id: str | None = None
    transcript: str = ""


class ResponseCreated(_Incoming):
    type: Literal["response.created"]


class OutputAudioTranscriptDelta(_Incoming):
    type: Literal["response.output_audio_transcript.delta"]
    item_id: str | None = None
    delta: str


class OutputAudioDelta(_Incoming):
    """Only seen with JSON audio transport; Symphony asks for binary frames."""

    type: Literal["response.output_audio.delta"]
    delta: str


class FunctionCallArgumentsDone(_Incoming):
    type: Literal["response.function_call_arguments.done"]
    call_id: str
    name: str
    arguments: str


class ResponseDone(_Incoming):
    type: Literal["response.done"]


class ErrorDetail(_Incoming):
    message: str = "unknown error"


class ErrorEvent(_Incoming):
    type: Literal["error"]
    error: ErrorDetail = ErrorDetail()


GrokServerEvent = Annotated[
    Union[
        SessionUpdated,
        SpeechStarted,
        SpeechStopped,
        InputTranscriptionCompleted,
        ResponseCreated,
        OutputAudioTranscriptDelta,
        OutputAudioDelta,
        FunctionCallArgumentsDone,
        ResponseDone,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]
grok_server_event_adapter: TypeAdapter[GrokServerEvent] = TypeAdapter(GrokServerEvent)

HANDLED_SERVER_EVENTS = frozenset(
    {
        "session.updated",
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
        "conversation.item.input_audio_transcription.completed",
        "response.created",
        "response.output_audio_transcript.delta",
        "response.output_audio.delta",
        "response.function_call_arguments.done",
        "response.done",
        "error",
    }
)


class EventEnvelope(_Incoming):
    type: str


# ---------------------------------------------------------------- Symphony -> server


class _Outgoing(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AudioFormat(_Outgoing):
    type: Literal["audio/pcm"] = "audio/pcm"
    rate: int = 24000


class AudioDirection(_Outgoing):
    format: AudioFormat = AudioFormat()
    transport: Literal["binary", "json"] = "binary"


class AudioConfig(_Outgoing):
    input: AudioDirection = AudioDirection()
    output: AudioDirection = AudioDirection()


class TurnDetection(_Outgoing):
    type: Literal["server_vad"] = "server_vad"


class Reasoning(_Outgoing):
    """`reasoning.effort` on session.update (docs.x.ai voice agent guide, Session Parameters)."""

    effort: Literal["none", "low", "medium", "high"]


class SessionConfig(_Outgoing):
    voice: str
    instructions: str
    turn_detection: TurnDetection = TurnDetection()
    audio: AudioConfig = AudioConfig()
    tools: list[dict[str, Any]]
    reasoning: Reasoning | None = None


class SessionUpdate(_Outgoing):
    type: Literal["session.update"] = "session.update"
    session: SessionConfig


class InputText(_Outgoing):
    type: Literal["input_text"] = "input_text"
    text: str


class UserMessageItem(_Outgoing):
    type: Literal["message"] = "message"
    role: Literal["user"] = "user"
    content: list[InputText]


class OutputText(_Outgoing):
    type: Literal["output_text"] = "output_text"
    text: str


class AssistantMessageItem(_Outgoing):
    """An earlier assistant turn, replayed into a new upstream session on a model hot-swap."""

    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: list[OutputText]


class FunctionCallOutputItem(_Outgoing):
    type: Literal["function_call_output"] = "function_call_output"
    call_id: str
    output: str


class ConversationItemCreate(_Outgoing):
    type: Literal["conversation.item.create"] = "conversation.item.create"
    item: Union[UserMessageItem, AssistantMessageItem, FunctionCallOutputItem]


class ResponseCreate(_Outgoing):
    type: Literal["response.create"] = "response.create"


class ResponseCancel(_Outgoing):
    type: Literal["response.cancel"] = "response.cancel"


GrokClientEvent = Union[SessionUpdate, ConversationItemCreate, ResponseCreate, ResponseCancel]

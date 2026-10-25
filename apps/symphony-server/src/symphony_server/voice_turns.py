"""Harness turns on a live voice session: the link a run reaches by session id, and the call
that sends a turn to it and maps its response back to core_ai StreamEvents.

core_ai.voice only speaks the model protocol; what the session has been told, and which run
owns a response, is kept here (osmo), on the harness's existing provider API.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
from typing import Any

from core_ai.content import text_from_content
from core_ai.types import Message, StreamEvent
from core_ai.voice import ResponseDone, TextDelta, ToolCall, VoiceSession
from pydantic import BaseModel


@dataclass(eq=False)
class VoiceLink:
    """A voice session opened for one harness session, and what it has already been told.

    `heard` holds transcripts of speech the session took in as audio; the bridge adds each one
    so the run answering it does not send the words again."""

    model_id: str
    session: VoiceSession
    synced: bool = False
    heard: list[str] = field(default_factory=list)


class VoiceLinks:
    """Open voice links by harness session id; runs reach theirs through VoiceRunLink."""

    def __init__(self) -> None:
        self._links: dict[str, VoiceLink] = {}

    def open(self, session_id: str, model_id: str, session: VoiceSession) -> VoiceLink:
        link = VoiceLink(model_id, session)
        self._links[session_id] = link
        return link

    def close(self, session_id: str, link: VoiceLink) -> None:
        if self._links.get(session_id) is link:
            del self._links[session_id]

    def get(self, session_id: str) -> VoiceLink | None:
        return self._links.get(session_id)


class _ToolCallFunction(BaseModel):
    name: str
    arguments: str = "{}"


class _HistoryToolCall(BaseModel):
    """One entry of `Message.tool_calls` (the OpenAI-style shape the harness records)."""

    id: str
    function: _ToolCallFunction


class VoiceTurn:
    """Sends only what the voice session has not seen, then maps its response to StreamEvents.

    Speech the session already heard (and, with server VAD, already answered) is not sent again.
    The first turn on a fresh session replays the earlier history (text, tool calls and their
    results), so the harness history, already compacted against the voice model's configured
    window, stays the source of truth across reconnects and model swaps."""

    def __init__(self, link: VoiceLink) -> None:
        self.link = link
        self.session = link.session

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncGenerator[StreamEvent, None]:
        await self._configure(messages, tools)
        await self._send_turn(messages)
        index = 0
        async for event in self.session.response():
            if isinstance(event, TextDelta):
                yield StreamEvent(type="text_delta", delta=event.text)
            elif isinstance(event, ToolCall):
                yield StreamEvent(
                    type="toolcall_start",
                    content_index=index,
                    tool_call_id=event.call_id,
                    tool_name=event.name,
                )
                yield StreamEvent(
                    type="toolcall_arguments",
                    content_index=index,
                    tool_call_id=event.call_id,
                    delta=event.arguments,
                )
                index += 1
            elif isinstance(event, ResponseDone):
                if event.error:
                    raise RuntimeError(f"voice response failed: {event.error}")
                if event.input_tokens is not None or event.output_tokens is not None:
                    yield usage_event(event)
                yield StreamEvent(type="done")

    async def _configure(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> None:
        instructions = "\n\n".join(
            text_from_content(m.content) for m in messages if m.role == "system"
        )
        config = replace(
            self.session.config, instructions=instructions, tools=tuple(tools)
        )
        if config != self.session.config:
            await self.session.update(config)

    async def _send_turn(self, messages: list[Message]) -> None:
        conversation = [m for m in messages if m.role != "system"]
        new = trailing_turn(conversation)
        if not self.link.synced:
            for message in conversation[: len(conversation) - len(new)]:
                await self._replay(message)
            self.link.synced = True
        if new and new[0].role == "tool":
            for message in new:
                await self.session.add_tool_result(
                    str(message.tool_call_id), text_from_content(message.content)
                )
        elif new:
            text = text_from_content(new[0].content)
            if text in self.link.heard:
                self.link.heard.remove(text)
                if self.session.config.turn_detection == "server_vad":
                    return  # the session heard this speech and is already answering it
            else:
                await self.session.add_message("user", text)
        await self.session.respond()

    async def _replay(self, message: Message) -> None:
        text = text_from_content(message.content)
        if message.role == "tool":
            await self.session.add_tool_result(str(message.tool_call_id), text)
            return
        if message.role in ("user", "assistant") and text:
            await self.session.add_message(
                "user" if message.role == "user" else "assistant", text
            )
        for call in message.tool_calls or []:
            parsed = _HistoryToolCall.model_validate(call)
            await self.session.add_tool_call(
                parsed.id, parsed.function.name, parsed.function.arguments
            )


def trailing_turn(conversation: list[Message]) -> list[Message]:
    """The messages a turn answers: trailing tool results, or the last user message."""
    tools: list[Message] = []
    for message in reversed(conversation):
        if message.role != "tool":
            break
        tools.insert(0, message)
    if tools:
        return tools
    return conversation[-1:] if conversation and conversation[-1].role == "user" else []


def usage_event(done: ResponseDone) -> StreamEvent:
    prompt, completion = done.input_tokens or 0, done.output_tokens or 0
    return StreamEvent(
        type="usage",
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )

"""The voice turns of a session: reading what the voice model says and does, answer by answer.

The voice model answers on its own (server VAD). Each answer is shown and stored, and the tools it calls run and go
back to it with their results. Barge-in stops the speech only (never an agent run); a tool round started before it
does not ask for another answer.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any, Protocol

from core_ai.voice import (
    AudioDelta,
    ResponseDone,
    SpeechStarted,
    SpeechStopped,
    TextDelta,
    ToolCall,
    UserTranscript,
    VoiceError,
    VoiceEvent,
    VoiceSession,
)
from starlette.websockets import WebSocket, WebSocketDisconnect

from symphony_server.grok_compat import ReasoningDelta
from symphony_server.protocol import OsmoState, ServerEvent, TranscriptDelta
from symphony_server.voice_switch import VoiceSwitch
from symphony_server.voice_tool_calls import VoiceToolCalls
from symphony_server.voice_transcript import Transcript
from symphony_server.voices import Selection, VoiceModel

log = logging.getLogger("symphony.voice")

# `state` detail telling the client to drop queued assistant audio immediately.
BARGE_IN = "barge-in"
# An utterance's bubble waits this long for its transcript (some never come: noise, a failed
# transcription, a closed voice session) before it is dropped.
UNHEARD_WAIT_S = 10.0
# Tools whose result needs no answer when the answer that called them already said something:
# "I'll look into it" is enough, and a second line after run_agent returns only repeats it.
ANNOUNCED_TOOLS = frozenset({"run_agent"})


@dataclass
class Response:
    """One voice model response as read off the session."""

    # The answer's bubble in the browser: its deltas carry this id, and a `final` one ends it.
    item_id: str = field(default_factory=lambda: f"a-{secrets.token_hex(6)}")
    text: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    done: ResponseDone | None = None
    # Utterances still awaiting their transcript when the response began; stored after them.
    after: frozenset[str] | None = None


class TurnHost(Protocol):
    """What the turns need from the session that runs them (voice_bridge.VoiceBridge)."""

    voice: VoiceSession | None
    selection: Selection
    switch: VoiceSwitch

    async def emit(self, event: ServerEvent) -> None:
        """Send an event to the browser."""

    async def set_state(self, state: OsmoState, detail: str | None = None) -> None:
        """Tell the browser Osmo's state."""

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        """Run `work` beside the session."""

    def model(self) -> VoiceModel:
        """The selected voice model."""

    async def title_from(self, text: str) -> None:
        """Name an untitled thread after the first thing said in it."""

    async def record_usage(self, done: ResponseDone) -> None:
        """Store and show what an answer used."""

    async def voice_lost(self) -> None:
        """The voice session ended on its own."""


class VoiceTurns:
    """Reads one voice session's events and answers for as long as it is the session's voice."""

    def __init__(self, host: TurnHost, browser: WebSocket, transcript: Transcript, calls: VoiceToolCalls) -> None:
        self.host, self.browser, self.transcript, self.calls = host, browser, transcript, calls
        # Set while no answer is under way: model switches wait for it.
        self.idle = asyncio.Event()
        self.idle.set()
        # Bumped on every barge-in, so a tool round started before it does not ask for another answer.
        self.barge_ins = 0
        # Cancellation must also unblock a response reader if the provider omits response.done.
        self.response_task: asyncio.Task[Response] | None = None
        self.response_active = False
        # A model switch asked for by set_voice mid-answer; applied once the answer ends.
        self.swap_after_turn = False

    async def pump(self, voice: VoiceSession) -> None:
        """Handle the session's events until it closes; a session that closes on its own is lost."""
        async for event in voice.events():
            await self.on_voice_event(voice, event)
        if self.host.voice is voice:
            await self.host.voice_lost()

    async def cancel_response(self, voice: VoiceSession) -> None:
        """Stop the answer under way, including a reader waiting on a response.done that may never come."""
        await voice.cancel()
        if voice is self.host.voice and self.response_task is not None and self.response_active:
            self.response_task.cancel()

    async def cut_off(self, voice: VoiceSession) -> None:
        """Barge in on the answer under way: stop it, and keep a tool round already running from asking for another."""
        self.barge_ins += 1
        await self.cancel_response(voice)

    async def on_voice_event(self, voice: VoiceSession, event: VoiceEvent) -> None:
        """React to speech starting and stopping, transcripts, audio and errors."""
        if isinstance(event, SpeechStarted):
            # Barge-in: stop the speech (never an agent run) and tell the client to flush its
            # playback queue now (a plain `listening` at the end of a turn must not cut audio off).
            await self.cut_off(voice)
            await self.host.set_state("listening", BARGE_IN)
        elif isinstance(event, SpeechStopped):
            self.idle.clear()
            utterance = self.transcript.next_utterance()
            self.transcript.unheard.append(utterance)
            self.transcript.open_trace(utterance, self.host.model(), self.host.selection.voice)
            await self.host.emit(TranscriptDelta(role="user", text="", itemId=utterance))
            self.host.spawn(self.expire(utterance))
            await self.host.set_state("thinking")
        elif isinstance(event, UserTranscript):
            utterance = self.transcript.unheard.pop(0) if self.transcript.unheard else self.transcript.next_utterance()
            await self.host.emit(TranscriptDelta(role="user", text=event.text, itemId=utterance, final=True))
            if event.text:
                self.transcript.record("user", event.text)
                await self.host.title_from(event.text)
            self.transcript.finish_trace(utterance, event.text)
            self.transcript.store_held()
            if not event.text:
                # Noise, not words: drop whatever the model started saying about it.
                await self.cancel_response(voice)
                await self.turn_over()
        elif isinstance(event, AudioDelta):
            await self.host.set_state("speaking")
            try:
                await self.browser.send_bytes(event.audio)
            except (WebSocketDisconnect, RuntimeError):
                log.info("browser gone while sending audio")
        elif isinstance(event, VoiceError):
            log.error("voice model error: %s", event.message)

    async def expire(self, utterance: str) -> None:
        """Close an utterance's bubble when its transcript never comes."""
        await asyncio.sleep(UNHEARD_WAIT_S)
        if self.transcript.forget(utterance):
            await self.host.emit(TranscriptDelta(role="user", text="", itemId=utterance, final=True))

    async def answer(self, voice: VoiceSession) -> None:
        """Read the session's responses in order for as long as it is open: show and store each
        answer, and run the tools it calls, asking for the next answer with their results."""
        while self.host.voice is voice:
            task = asyncio.create_task(self.read_response(voice))
            self.response_task = task
            try:
                response = await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
                continue
            finally:
                if self.response_task is task:
                    self.response_task = None
                    self.response_active = False
            if response.done is None or self.host.voice is not voice:
                return
            await self.host.record_usage(response.done)
            if response.text:
                text = "".join(response.text)
                self.transcript.note_answer(response.after, text)
                self.transcript.hold(response.after or frozenset(), text)
            if response.calls and response.done.error is None:
                await self.run_tools(voice, response.calls, spoke=bool(response.text))
            else:
                await self.turn_over()

    async def read_response(self, voice: VoiceSession) -> Response:
        """Read one response off the session, showing its text and reasoning as they come."""
        response = Response()
        async for event in voice.response():
            self.response_active = True
            if response.after is None:
                response.after = frozenset(self.transcript.unheard)
            if isinstance(event, TextDelta):
                response.text.append(event.text)
                await self.host.emit(TranscriptDelta(role="assistant", text=event.text, itemId=response.item_id))
            elif isinstance(event, ReasoningDelta):
                response.reasoning.append(event.text)
                await self.host.emit(TranscriptDelta(role="assistant", text="", reasoning=event.text, itemId=response.item_id))
            elif isinstance(event, ToolCall):
                response.calls.append(event)
            else:
                response.done = event
        if response.text or response.reasoning:
            await self.host.emit(
                TranscriptDelta(
                    role="assistant",
                    text="".join(response.text),
                    reasoning="".join(response.reasoning) or None,
                    itemId=response.item_id,
                    final=True,
                )
            )
        return response

    async def run_tools(self, voice: VoiceSession, calls: list[ToolCall], *, spoke: bool) -> None:
        """Run an answer's tool calls and hand the results back, asking for the next answer unless that would repeat it."""
        self.idle.clear()
        barge_ins = self.barge_ins
        for call in calls:
            await voice.add_tool_result(call.call_id, await self.calls.run(call))
        if self.barge_ins != barge_ins:
            return  # the user spoke meanwhile: the answer server VAD starts for that sees the results
        if spoke and all(call.name in ANNOUNCED_TOOLS for call in calls):
            await self.turn_over()
        else:
            await voice.respond()

    async def turn_over(self) -> None:
        """No answer is under way: apply a deferred model switch, and go back to listening."""
        self.idle.set()
        if self.swap_after_turn:
            self.swap_after_turn = False
            await self.host.switch.apply_swap("tool")
        await self.host.set_state("listening")

    async def ask(self, voice: VoiceSession) -> None:
        """Ask the model for an answer now."""
        self.idle.clear()
        await voice.respond()

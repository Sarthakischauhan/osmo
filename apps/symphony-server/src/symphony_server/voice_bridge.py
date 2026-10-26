"""One Osmo voice session: the browser socket on one side, a core_ai voice session and
core_server harness runs on the other.

The voice session is audio in and out only. Every user turn (a finished utterance or a
widget tap) is a harness run on this bridge's session id, so the harness owns history, tools
and the model call; the run reaches the open voice session through its `VoiceLink` (osmo's
`VoiceLinks`, handed to each run by a `VoiceRunLink` addon). The bridge records what the
session heard on the link, decides which run answers, and maps run events to wire events.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Coroutine
from typing import Any, Literal

from core_ai.voice import (
    AudioDelta,
    SpeechStarted,
    SpeechStopped,
    UserTranscript,
    VoiceConfig,
    VoiceError,
    VoiceEvent,
    VoiceSession,
)
from core_harness import ControlPlaneEvent
from core_server import InProcessRunBackend, RunCapacityError, RunContext, RunRequest, RunSubmission, ServerConfig
from pydantic import ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import WebSocketException

from symphony_server.canvas import OSMO_WIDGET, WidgetBoard
from symphony_server.model_access import ModelAccess
from symphony_server.protocol import (
    Interrupt,
    ModelsEvent,
    OsmoState,
    ServerEvent,
    SessionConfigure,
    SessionEnd,
    SessionStart,
    StateEvent,
    TranscriptDelta,
    VoiceConfigured,
    VoicePreviewRequest,
    WidgetAction,
    client_event_adapter,
    widget_event_adapter,
)
from symphony_server.session_previews import SessionPreviews
from symphony_server.voice_turns import VoiceLink, VoiceLinks
from symphony_server.turn_events import RunFailedPayload, TextDeltaPayload, ToolStartedPayload, action_text, activity_label
from symphony_server.voices import (
    OSMO_VOICE,
    PreviewCache,
    Selection,
    SetVoiceArgs,
    VoiceModel,
    core_model_id,
    default_selection,
    find_model,
    resolve_selection,
)

log = logging.getLogger("symphony.voice")

# `state` detail telling the client to drop queued assistant audio immediately.
BARGE_IN = "barge-in"
# A picker model switch waits this long for the current turn to finish.
SWAP_WAIT_S = 15.0


class VoiceBridge:
    def __init__(
        self,
        browser: WebSocket,
        *,
        core_config: ServerConfig,
        backend: InProcessRunBackend,
        access: ModelAccess,
        board: WidgetBoard,
        links: VoiceLinks,
        previews: PreviewCache,
        default_voice: str,
    ) -> None:
        self.browser, self.core_config, self.backend = browser, core_config, backend
        self.access, self.board, self.links = access, board, links
        self.previews = SessionPreviews(access, previews)
        self.default_voice = default_voice
        # Every run of this browser socket shares it, so the harness keeps one conversation.
        self.session_id = f"osmo-{secrets.token_hex(8)}"
        self.voice: VoiceSession | None = None
        self.link: VoiceLink | None = None
        self.selection = default_selection(access.models(), default_voice)
        self.state: OsmoState = "idle"
        self.state_detail: str | None = None
        self.run_id: str | None = None
        self.turn_idle = asyncio.Event()
        self.turn_idle.set()
        # A model switch asked for by set_voice mid-turn; applied once the turn ends.
        self.swap_after_turn = False
        self.configure_lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------ browser side

    async def run(self) -> None:
        models = self.access.models()
        await self.emit(ModelsEvent(models=list(models), selection=self.selection))
        try:
            while True:
                message = await self.browser.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                text = message.get("text")
                if data is not None:
                    await self.forward_audio(data)
                elif text is not None:
                    await self.handle_client_text(text)
        except WebSocketDisconnect:
            pass
        finally:
            for task in tuple(self.tasks):
                task.cancel()
            await self.close_voice()

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def forward_audio(self, pcm: bytes) -> None:
        if self.voice is None or self.previews.mic_paused():
            return
        try:
            await self.voice.send_audio(pcm)
        except WebSocketException:
            await self.voice_lost()

    async def handle_client_text(self, text: str) -> None:
        try:
            event = client_event_adapter.validate_json(text)
        except ValidationError as exc:
            log.warning("bad client event: %s", exc.errors()[:1])
            return
        if isinstance(event, SessionStart):
            await self.start(event)
        elif isinstance(event, SessionEnd):
            await self.close_voice()
            await self.set_state("idle")
        elif isinstance(event, Interrupt):
            await self.interrupt()
        elif isinstance(event, WidgetAction):
            await self.widget_action(event)
        elif isinstance(event, SessionConfigure):
            # Beside the receive loop: a model switch may wait for the current turn to end.
            self.spawn(self.configure_from_client(SetVoiceArgs(model=event.model, voice=event.voice, effort=event.effort)))
        elif isinstance(event, VoicePreviewRequest):
            self.spawn(self.preview(event))

    async def emit(self, event: ServerEvent) -> None:
        try:
            await self.browser.send_text(event.model_dump_json(exclude_none=True))
        except (WebSocketDisconnect, RuntimeError):
            log.info("browser gone while sending %s", event.type)

    async def set_state(self, state: OsmoState, detail: str | None = None) -> None:
        if state == self.state and detail == self.state_detail:
            return
        self.state, self.state_detail = state, detail
        await self.emit(StateEvent(state=state, detail=detail))

    # ------------------------------------------------------------ voice session

    async def start(self, event: SessionStart) -> None:
        if self.voice is not None:
            return
        if not self.access.connect("xai"):
            await self.set_state("idle", "xAI is not connected: add a key in Providers")
            return
        change = SetVoiceArgs(model=event.model, voice=event.voice, effort=event.effort)
        picked = resolve_selection(self.access.models(), change, self.selection)
        if isinstance(picked, Selection):
            self.selection = picked
        if not await self.open_voice():
            await self.set_state("idle", "could not reach the voice model")
            return
        await self.set_state("listening")
        await self.emit(VoiceConfigured(selection=self.selection, source="client"))

    def model(self) -> VoiceModel:
        models = self.access.models()
        return find_model(models, self.selection.model) or models[0]

    def voice_config(self) -> VoiceConfig:
        """The harness sends the same prompt and tools each turn, so its model call leaves them as they are."""
        return VoiceConfig(
            voice=self.selection.voice,
            instructions=self.core_config.system_prompt,
            tools=tuple(tool.get_schema() for tool in self.core_config.tools),
            reasoning_effort=self.selection.effort,
        )

    async def open_voice(self) -> bool:
        """Open a voice session on the selected model, link it to this bridge's session id
        (replacing any earlier link there) and start reading it."""
        model = self.model()
        if not self.access.connect(model.provider):
            return False
        try:
            voice = await self.access.open_voice(model, self.voice_config())
        except (OSError, WebSocketException) as exc:
            log.error("could not open the voice session on %s: %s", model.id, exc)
            return False
        self.voice = voice
        self.link = self.links.open(self.session_id, core_model_id(model), voice)
        log.info("voice session open (model %s)", model.id)
        self.spawn(self.pump(voice))
        return True

    async def swap(self) -> bool:
        """Move onto a newly selected model. The model is fixed per voice session, so open a new
        one; it is seeded from the harness history on the next turn. The browser socket stays."""
        old, old_link = self.voice, self.link
        if not await self.open_voice():
            return False
        if old_link is not None:
            self.links.close(self.session_id, old_link)
        if old is not None:
            await old.aclose()
        return True

    async def close_voice(self) -> None:
        voice, self.voice = self.voice, None
        link, self.link = self.link, None
        await self.cancel_run()
        if link is not None:
            self.links.close(self.session_id, link)
        if voice is not None:
            await voice.aclose()

    async def voice_lost(self) -> None:
        await self.close_voice()
        await self.set_state("idle", "voice session ended")

    async def pump(self, voice: VoiceSession) -> None:
        async for event in voice.events():
            await self.on_voice_event(voice, event)
        if self.voice is voice:
            await self.voice_lost()

    async def on_voice_event(self, voice: VoiceSession, event: VoiceEvent) -> None:
        if isinstance(event, SpeechStarted):
            # Barge-in: stop the answer and tell the client to flush its playback queue now
            # (a plain `listening` at the end of a turn must not cut off audio still playing).
            await self.stop_turn(voice)
            await self.set_state("listening", BARGE_IN)
        elif isinstance(event, SpeechStopped):
            await self.set_state("thinking")
        elif isinstance(event, UserTranscript):
            if event.text:
                if self.link is not None and self.link.session is voice:
                    self.link.heard.append(event.text)  # spoken as audio: the run must not resend it
                await self.emit(TranscriptDelta(role="user", text=event.text))
                await self.submit(event.text)
            else:
                # Noise, not words: drop whatever the model started saying about it.
                await voice.cancel()
                await self.set_state("listening")
        elif isinstance(event, AudioDelta):
            await self.set_state("speaking")
            try:
                await self.browser.send_bytes(event.audio)
            except (WebSocketDisconnect, RuntimeError):
                log.info("browser gone while sending audio")
        elif isinstance(event, VoiceError):
            log.error("voice model error: %s", event.message)

    # ------------------------------------------------------------ turns (harness runs)

    async def submit(self, text: str) -> None:
        """Run one turn. The run's model call answers through the open voice session."""
        await self.cancel_run()
        model = self.model()
        request = RunRequest(message=text, session_id=self.session_id, model_id=core_model_id(model))
        try:
            record = await self.backend.submit(RunSubmission(RunContext(session_id=self.session_id), request))
        except RunCapacityError as exc:
            log.error("turn not started: %s", exc)
            await self.set_state("listening", "the server is busy; try again")
            return
        self.run_id = record.run_id
        self.turn_idle.clear()
        self.spawn(self.follow(record.run_id))

    async def follow(self, run_id: str) -> None:
        async for _, event in self.backend.events(run_id):
            await self.on_run_event(event)
        if self.run_id != run_id:
            return
        self.run_id = None
        self.turn_idle.set()
        if self.swap_after_turn:
            self.swap_after_turn = False
            await self.apply_swap("tool")
        await self.set_state("listening")

    async def on_run_event(self, event: ControlPlaneEvent) -> None:
        if event.event_type == "text_delta":
            await self.emit(TranscriptDelta(role="assistant", text=TextDeltaPayload.model_validate(event.payload).delta))
        elif event.event_type == "tool_execution_started":
            started = ToolStartedPayload.model_validate(event.payload)
            await self.set_state("working", activity_label(started.tool_name, started.arguments))
        elif event.event_type == OSMO_WIDGET:
            await self.emit(widget_event_adapter.validate_python(event.payload))
        elif event.event_type == OSMO_VOICE:
            await self.configure(SetVoiceArgs.model_validate(event.payload), "tool")
        elif event.event_type == "run_failed":
            log.warning("turn failed: %s", RunFailedPayload.model_validate(event.payload).message)

    async def cancel_run(self) -> None:
        run_id, self.run_id = self.run_id, None
        self.turn_idle.set()
        if run_id is not None:
            await self.backend.cancel(run_id)

    async def stop_turn(self, voice: VoiceSession) -> None:
        await voice.cancel()
        await self.cancel_run()

    async def interrupt(self) -> None:
        if self.voice is None:
            return
        await self.stop_turn(self.voice)
        await self.set_state("listening", BARGE_IN)

    async def widget_action(self, action: WidgetAction) -> None:
        widget = self.board.get(action.id)
        if self.voice is None or widget is None:
            return
        await self.stop_turn(self.voice)
        await self.set_state("thinking")
        await self.submit(action_text(action, widget))

    # ------------------------------------------------------------ model, voice and effort

    async def configure_from_client(self, change: SetVoiceArgs) -> None:
        """A picker commit. A model switch waits for the current turn to finish (never
        cancelling it), then swaps and tells the browser the session is listening."""
        async with self.configure_lock:
            swapping = self.voice is not None and (change.model or self.selection.model) != self.selection.model
            if swapping:
                try:
                    async with asyncio.timeout(SWAP_WAIT_S):
                        await self.turn_idle.wait()
                except TimeoutError:
                    log.warning("model switch waited %.0fs for the turn to end; switching anyway", SWAP_WAIT_S)
            before = self.voice
            outcome = await self.configure(change, "client")
            if isinstance(outcome, Selection) and self.voice is not before:
                await self.set_state("listening")

    async def configure(self, change: SetVoiceArgs, source: Literal["client", "tool"]) -> Selection | str:
        """The one path for picker commits and `set_voice`: validate, apply, tell the browser.
        The browser always hears back with the applied selection, or the one still in effect plus `error`."""
        picked = resolve_selection(self.access.models(), change, self.selection)
        if isinstance(picked, str):
            log.warning("rejected voice change from %s: %s", source, picked)
            await self.emit(VoiceConfigured(selection=self.selection, source=source, error=picked))
            return picked
        previous, self.selection = self.selection, picked
        if self.voice is None:
            await self.emit(VoiceConfigured(selection=picked, source=source))
            return picked
        if picked.model == previous.model:
            await self.voice.update(self.voice_config())
        elif source == "tool" and self.run_id is not None:
            # The turn that asked is still speaking its confirmation on this session.
            self.swap_after_turn = True
            return picked
        elif not await self.swap():
            self.selection = previous
            error = f"could not open {picked.model}; still on {previous.model}"
            await self.emit(VoiceConfigured(selection=previous, source=source, error=error))
            return error
        await self.emit(VoiceConfigured(selection=picked, source=source))
        return picked

    async def apply_swap(self, source: Literal["client", "tool"]) -> None:
        error = None if await self.swap() else f"could not open {self.selection.model}"
        await self.emit(VoiceConfigured(selection=self.selection, source=source, error=error))

    async def preview(self, request: VoicePreviewRequest) -> None:
        await self.emit(await self.previews.play(request, self.state))

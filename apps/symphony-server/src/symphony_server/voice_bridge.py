"""One Osmo voice session: the browser socket on one side, a core_ai voice session on the other.

The bridge owns the voice turns. The voice model answers on its own (server VAD) and calls its
quick tools (search, weather, stocks, widgets, set_voice), which the bridge runs directly and
hands back. Longer work goes to a background agent (`run_agent`, see agents.py): the bridge is
the observer of its thread's agent runs, shows their events in the browser and tells the voice
model about them, as `conversation.item.create` plus `response.create`, at the moment each
notice's tag asks for. Barge-in stops the speech only; agent runs keep going.

Every utterance, answer and agent notice is stored on the session's thread (storage.py); a new
voice session (reconnect or model switch) is seeded from it.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Coroutine, Sequence
from typing import Any

from core_ai.voice import ResponseDone, VoiceConfig, VoiceSession
from core_harness import Tool
from pydantic import ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import WebSocketException

from symphony_server.agents import AgentDesk, AgentRuns, AgentStatusTool, CancelAgentTool, Notice, RunAgentTool
from symphony_server.approvals import ApprovalBroker
from symphony_server.canvas import WidgetBoard
from symphony_server.desktop import DesktopSession
from symphony_server.model_access import ModelAccess
from symphony_server.music import MusicNow
from symphony_server.protocol import (
    BrowserClick,
    BrowserClose,
    BrowserKey,
    BrowserScroll,
    BrowserTakeover,
    DesktopConfigure,
    DesktopResult,
    Interrupt,
    ModelsEvent,
    MusicState,
    OsmoState,
    ServerEvent,
    SessionConfigure,
    SessionEnd,
    SessionStart,
    StateEvent,
    ThreadEvent,
    UsageEvent,
    VoiceConfigured,
    VoicePreviewRequest,
    WidgetAction,
    client_event_adapter,
)
from symphony_server.providers import provider_label
from symphony_server.session_previews import SessionPreviews
from symphony_server.storage import DEFAULT_THREAD_TITLE, Storage, ThreadRecord
from symphony_server.turn_events import action_text
from symphony_server.usage import voice_usage
from symphony_server.voice_switch import VoiceSwitch
from symphony_server.voice_tool_calls import VoiceToolCalls, VoiceToolSink
from symphony_server.voice_transcript import Transcript, conversation_title
from symphony_server.voice_turns import BARGE_IN, VoiceTurns
from symphony_server.voices import PreviewCache, Selection, SetVoiceArgs, VoiceModel, default_selection, find_model, resolve_selection

log = logging.getLogger("symphony.voice")


class VoiceBridge:
    """One browser socket's voice session: its thread, its voice model session and what the browser asks of it."""

    def __init__(
        self,
        browser: WebSocket,
        *,
        instructions: str,
        tools: Sequence[Tool],
        agents: AgentRuns,
        storage: Storage,
        access: ModelAccess,
        board: WidgetBoard,
        previews: PreviewCache,
        default_voice: str,
        broker: ApprovalBroker,
        approval_tools: frozenset[str],
        music: MusicNow,
    ) -> None:
        self.browser, self.instructions = browser, instructions
        # What the app's music player last said it plays (for music_status).
        self.music = music
        self.desktop = DesktopSession(self.emit)
        self.agents, self.storage, self.access, self.board = agents, storage, access, board
        self.previews = SessionPreviews(access, previews)
        self.desk = AgentDesk(agents, desktop=self.desktop)
        self.transcript = Transcript(storage, instructions)
        self.switch = VoiceSwitch(self)
        sink = VoiceToolSink(self.emit, self.transcript, self.switch.configure)
        voice_tools = [*tools, RunAgentTool(self.desk), AgentStatusTool(self.desk), CancelAgentTool(self.desk)]
        self.broker = broker
        self.calls = VoiceToolCalls(
            voice_tools,
            broker=broker,
            approval_tools=approval_tools,
            session_id=f"voice-{secrets.token_hex(6)}",
            desktop=self.desktop,
            sink=sink,
            emit=self.emit,
            set_state=self.set_state,
        )
        self.thread: ThreadRecord | None = None
        self.voice: VoiceSession | None = None
        self.selection = default_selection(access.models(), default_voice)
        self.state: OsmoState = "idle"
        self.state_detail: str | None = None
        self.turns = VoiceTurns(self, browser, self.transcript, self.calls)
        self.tasks: set[asyncio.Task[None]] = set()
        # Background completions never preempt an active user turn. Drain these at turn_over.
        self.pending_notices: list[Notice] = []
        self.notice_lock = asyncio.Lock()
        self.audio_frames = 0

    async def run(self) -> None:
        """Serve the browser socket until it closes, then stop everything the session started."""
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
            if self.thread is not None:
                self.agents.unobserve(self.thread.id, self)
            self.broker.deny_session(self.calls.approvals.session_id)
            await self.close_voice()

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        """Run `work` beside the receive loop, cancelled when the socket closes."""
        task: asyncio.Task[None] = asyncio.create_task(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def forward_audio(self, pcm: bytes) -> None:
        """Send the browser's microphone audio to the voice session (not while a preview plays)."""
        if self.voice is None or self.previews.mic_paused():
            return
        try:
            await self.voice.send_audio(pcm)
            self.audio_frames += 1
            if self.audio_frames % 50 == 0:
                log.debug("voice PCM frames forwarded to provider: %d", self.audio_frames)
        except WebSocketException:
            await self.voice_lost()

    async def handle_client_text(self, text: str) -> None:
        """Act on one JSON event from the browser."""
        try:
            event = client_event_adapter.validate_json(text)
        except ValidationError as exc:
            # Where and what kind, never the input: a malformed browser.key would otherwise log what the user typed.
            log.warning("bad client event: %s", [(error["loc"], error["type"]) for error in exc.errors()[:3]])
            return
        if isinstance(event, DesktopConfigure):
            self.desktop.configure(event.enabled)
        elif isinstance(event, DesktopResult):
            self.desktop.resolve(event)
        elif isinstance(event, SessionStart):
            await self.start(event)
        elif isinstance(event, SessionEnd):
            await self.close_voice()
            await self.set_state("idle")
            # The thread's browser lives between runs and reconnects, and ends with the session, once the runs
            # still going on the thread have finished (they finish on their own and their results are stored).
            if self.thread is not None:
                await self.agents.browsers.end(self.thread.id)
        elif isinstance(event, (BrowserClick, BrowserScroll, BrowserKey)):
            if self.thread is not None:
                self.spawn(self.agents.browsers.drive(self.thread.id, event))
        elif isinstance(event, BrowserClose):
            if self.thread is not None and self.agents.browsers.widget_session(self.thread.id, event.id) is not None:
                await self.agents.browsers.close(self.thread.id)
        elif isinstance(event, MusicState):
            self.music.state = event
        elif isinstance(event, BrowserTakeover):
            if self.thread is not None and self.agents.browsers.widget_session(self.thread.id, event.id) is not None:
                await self.agents.browsers.take_over(self.thread.id, event.user)
        elif isinstance(event, Interrupt):
            await self.interrupt()
        elif isinstance(event, WidgetAction):
            await self.widget_action(event)
        elif isinstance(event, SessionConfigure):
            # Beside the receive loop: a model switch may wait for the current answer to end.
            self.spawn(self.switch.from_client(SetVoiceArgs(model=event.model, voice=event.voice, effort=event.effort)))
        elif isinstance(event, VoicePreviewRequest):
            self.spawn(self.preview(event))

    async def emit(self, event: ServerEvent) -> None:
        """Send an event to the browser (dropped when it is gone)."""
        try:
            await self.browser.send_text(event.model_dump_json(exclude_none=True))
        except (WebSocketDisconnect, RuntimeError):
            log.info("browser gone while sending %s", event.type)

    async def set_state(self, state: OsmoState, detail: str | None = None) -> None:
        """Tell the browser Osmo's state, when it changed."""
        if state == self.state and detail == self.state_detail:
            return
        self.state, self.state_detail = state, detail
        await self.emit(StateEvent(state=state, detail=detail))

    async def open_thread(self, thread_id: str | None) -> None:
        """Use the stored thread `thread_id` (a new one when unset or gone) and observe its agent runs."""
        thread = self.storage.get_thread(thread_id) if thread_id is not None else None
        if thread is None:
            thread = self.storage.create_thread()
        if self.thread is not None:
            self.agents.unobserve(self.thread.id, self)
        self.thread = thread
        self.transcript.thread_id = thread.id
        self.desk.thread_id = thread.id
        self.agents.observe(thread.id, self)
        await self.emit(ThreadEvent(thread=thread))
        # A browser the thread already has (a reconnect, a reload) is shown again, with the page it is on.
        session = self.agents.browsers.sessions.get(thread.id)
        self.agents.browsers.resume(thread.id)
        if session is not None:
            await self.emit(session.render())

    async def record_usage(self, done: ResponseDone) -> None:
        """Store and show what an answer used."""
        if self.thread is None:
            return
        model = self.model()
        usage = voice_usage(self.thread.id, f"{model.provider}:{model.id}", done)
        if usage is not None:
            self.storage.add_usage(usage)
            await self.emit(UsageEvent(usage=usage))

    async def title_from(self, text: str) -> None:
        """Name an untitled thread after the first thing said in it."""
        if self.thread is None or self.thread.title != DEFAULT_THREAD_TITLE:
            return
        title = conversation_title(text)
        if title is None:
            return
        renamed = self.storage.rename_thread(self.thread.id, title)
        if renamed is not None:
            self.thread = renamed
            await self.emit(ThreadEvent(thread=renamed))

    async def start(self, event: SessionStart) -> None:
        """Start the session: open the thread, apply the browser's picks and open the voice model."""
        if self.voice is not None:
            return
        await self.open_thread(event.threadId)
        change = SetVoiceArgs(model=event.model, voice=event.voice, effort=event.effort)
        picked = resolve_selection(self.access.models(), change, self.selection)
        if isinstance(picked, Selection):
            self.selection = picked
        provider = self.model().provider
        if not await self.access.prepare(provider, voice=True):
            await self.set_state("idle", f"{provider_label(provider)} is not connected: add a key in Providers")
            return
        if not await self.open_voice():
            await self.set_state("idle", "could not reach the voice model")
            return
        await self.set_state("listening")
        await self.emit(VoiceConfigured(selection=self.selection, source="client"))

    def model(self) -> VoiceModel:
        """The selected voice model (the first one when it is gone)."""
        models = self.access.models()
        return find_model(models, self.selection.model) or models[0]

    def voice_config(self) -> VoiceConfig:
        """The voice session's settings: the voice, the instructions, the tools and the effort."""
        return VoiceConfig(
            voice=self.selection.voice,
            instructions=self.instructions,
            tools=tuple(tool.get_schema() for tool in self.calls.tools.values()),
            reasoning_effort=self.selection.effort,
        )

    async def open_voice(self) -> bool:
        """Open a voice session on the selected model, seed it with the thread so far, and read it."""
        model = self.model()
        if not await self.access.prepare(model.provider, voice=True):
            return False
        try:
            voice = await self.access.open_voice(model, self.voice_config())
            await self.replay(voice)
        except (OSError, WebSocketException) as exc:
            log.error("could not open the voice session on %s: %s", model.id, exc)
            return False
        self.voice = voice
        log.info("voice session open (model %s)", model.id)
        self.spawn(self.turns.pump(voice))
        self.spawn(self.turns.answer(voice))
        return True

    async def replay(self, voice: VoiceSession) -> None:
        """Seed a new voice session with the thread so far."""
        if self.thread is None:
            return
        for message in self.storage.messages(self.thread.id):
            await voice.add_message("assistant" if message.role == "assistant" else "user", message.text)

    async def close_voice(self) -> None:
        """Close the voice session, storing the answers still held."""
        self.desktop.configure(False)
        voice, self.voice = self.voice, None
        self.turns.idle.set()
        # Transcripts still owed will not come now; keep the answers anyway.
        self.transcript.flush_held()
        self.pending_notices.clear()
        if voice is not None:
            await voice.aclose()

    async def voice_lost(self) -> None:
        """The voice session ended on its own: close it and tell the browser."""
        await self.close_voice()
        await self.set_state("idle", "voice session ended")

    async def interrupt(self) -> None:
        """The user's stop button: cut the answer off and listen."""
        if self.voice is None:
            return
        await self.turns.cut_off(self.voice)
        await self.set_state("listening", BARGE_IN)

    async def widget_action(self, action: WidgetAction) -> None:
        """A click in a widget, as the user's turn (once per widget)."""
        widget = self.board.get(action.id)
        if self.voice is None or widget is None:
            return
        text = action_text(action, widget)
        # One answer per widget: a second tap (or a frame script replaying one) is not another user turn.
        if text is None or not self.board.answer(action.id):
            return
        await self.turns.cut_off(self.voice)
        self.transcript.record("user", text)
        await self.set_state("thinking")
        await self.voice.add_message("user", text)
        await self.turns.ask(self.voice)

    async def show(self, event: ServerEvent) -> None:
        """Show an agent run's event in the browser (AgentObserver)."""
        await self.emit(event)

    async def tell(self, notice: Notice) -> None:
        """Queue delivery to voice while busy; the run manager has already persisted the notice.

        Background completion/approval must not cancel the current user turn or collide
        with realtime voice response generation.
        """
        if self.voice is None:
            return
        self.pending_notices.append(notice)
        if self.turns.idle.is_set():
            await self.flush_notices()

    async def flush_notices(self) -> bool:
        """Inject queued notices at most once when realtime voice is idle."""
        async with self.notice_lock:
            voice = self.voice
            if voice is None or not self.turns.idle.is_set() or not self.pending_notices:
                return False
            notices = self.pending_notices[:]
            self.pending_notices.clear()
            try:
                for notice in notices:
                    await voice.add_message("user", notice.text)
                if any(notice.tag == "interrupt" for notice in notices):
                    await self.set_state("thinking")
                    await self.turns.ask(voice)
            except Exception:
                self.pending_notices[:0] = notices
                log.exception("failed to relay background notices to voice")
                return False
            return True

    async def preview(self, request: VoicePreviewRequest) -> None:
        """Play a voice preview in the browser."""
        await self.emit(await self.previews.play(request, self.state))

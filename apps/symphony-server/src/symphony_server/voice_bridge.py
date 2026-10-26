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
import json
import logging
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from core_ai.voice import (
    AudioDelta,
    ResponseDone,
    SpeechStarted,
    SpeechStopped,
    TextDelta,
    ToolCall,
    UserTranscript,
    VoiceConfig,
    VoiceError,
    VoiceEvent,
    VoiceSession,
)
from core_harness import EventSink, Tool
from pydantic import ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import WebSocketException

from symphony_server.desktop import DesktopSession, RUN_DESKTOP
from symphony_server.agents import AgentDesk, AgentRuns, AgentStatusTool, CancelAgentTool, Notice, RunAgentTool
from symphony_server.canvas import OSMO_WIDGET, WidgetBoard
from symphony_server.model_access import ModelAccess
from symphony_server.providers import provider_label
from symphony_server.protocol import (
    DesktopConfigure,
    DesktopResult,
    Interrupt,
    ModelsEvent,
    OsmoState,
    ServerEvent,
    SessionConfigure,
    SessionEnd,
    SessionStart,
    StateEvent,
    ThreadEvent,
    TranscriptDelta,
    VoiceConfigured,
    VoicePreviewRequest,
    WidgetAction,
    client_event_adapter,
    widget_event_adapter,
)
from symphony_server.session_previews import SessionPreviews
from symphony_server.storage import DEFAULT_THREAD_TITLE, MessageRole, Storage, ThreadRecord
from symphony_server.turn_events import action_text, activity_label
from symphony_server.voices import (
    OSMO_VOICE,
    PreviewCache,
    Selection,
    SetVoiceArgs,
    VoiceModel,
    default_selection,
    find_model,
    resolve_selection,
)

log = logging.getLogger("symphony.voice")

# `state` detail telling the client to drop queued assistant audio immediately.
BARGE_IN = "barge-in"
# A picker model switch waits this long for the current answer to finish.
SWAP_WAIT_S = 15.0
# A thread is titled from its first utterance, cut to this length.
TITLE_FROM_SPEECH_MAX = 60


class VoiceToolSink(EventSink):
    """The sink a voice tool runs with: widget and voice events go straight to the bridge."""

    def __init__(self, bridge: VoiceBridge) -> None:
        super().__init__()
        self.bridge = bridge

    async def deliver(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == OSMO_WIDGET:
            await self.bridge.emit(widget_event_adapter.validate_python(payload))
        elif event_type == OSMO_VOICE:
            await self.bridge.configure(SetVoiceArgs.model_validate(payload), "tool")


@dataclass
class Response:
    """One voice model response as read off the session."""

    text: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    done: ResponseDone | None = None


def tool_output(result: Any) -> str:
    return result if isinstance(result, str) else json.dumps(result, default=str)


class VoiceBridge:
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
    ) -> None:
        self.browser, self.instructions = browser, instructions
        self.desktop = DesktopSession(self.emit)
        self.agents, self.storage, self.access, self.board = agents, storage, access, board
        self.previews = SessionPreviews(access, previews)
        self.desk = AgentDesk(agents, desktop=self.desktop)
        voice_tools = [*tools, RunAgentTool(self.desk), AgentStatusTool(self.desk), CancelAgentTool(self.desk)]
        self.tools = {tool.name: tool for tool in voice_tools}
        self.sink = VoiceToolSink(self)
        self.thread: ThreadRecord | None = None
        self.voice: VoiceSession | None = None
        self.selection = default_selection(access.models(), default_voice)
        self.state: OsmoState = "idle"
        self.state_detail: str | None = None
        # Set while no answer is under way: when_idle notices and model switches wait for it.
        self.idle = asyncio.Event()
        self.idle.set()
        # Bumped on every barge-in, so a tool round started before it does not ask for another answer.
        self.barge_ins = 0
        self.idle_notices: dict[str, Notice] = {}
        # A model switch asked for by set_voice mid-answer; applied once the answer ends.
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
            if self.thread is not None:
                self.agents.unobserve(self.thread.id, self)
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
        if isinstance(event, DesktopConfigure):
            self.desktop.configure(event.enabled)
        elif isinstance(event, DesktopResult):
            self.desktop.resolve(event)
        elif isinstance(event, SessionStart):
            await self.start(event)
        elif isinstance(event, SessionEnd):
            await self.close_voice()
            await self.set_state("idle")
        elif isinstance(event, Interrupt):
            await self.interrupt()
        elif isinstance(event, WidgetAction):
            await self.widget_action(event)
        elif isinstance(event, SessionConfigure):
            # Beside the receive loop: a model switch may wait for the current answer to end.
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

    # ------------------------------------------------------------ thread

    async def open_thread(self, thread_id: str | None) -> None:
        """Use the stored thread `thread_id` (a new one when unset or gone) and observe its agent runs."""
        thread = self.storage.get_thread(thread_id) if thread_id is not None else None
        if thread is None:
            thread = self.storage.create_thread()
        if self.thread is not None:
            self.agents.unobserve(self.thread.id, self)
        self.thread = thread
        self.desk.thread_id = thread.id
        self.agents.observe(thread.id, self)
        await self.emit(ThreadEvent(thread=thread))

    def record(self, role: MessageRole, text: str) -> None:
        if self.thread is not None:
            self.storage.add_message(self.thread.id, role, text)

    async def title_from(self, text: str) -> None:
        """Name an untitled thread after its first utterance."""
        if self.thread is None or self.thread.title != DEFAULT_THREAD_TITLE:
            return
        renamed = self.storage.rename_thread(self.thread.id, text[:TITLE_FROM_SPEECH_MAX])
        if renamed is not None:
            self.thread = renamed
            await self.emit(ThreadEvent(thread=renamed))

    # ------------------------------------------------------------ voice session

    async def start(self, event: SessionStart) -> None:
        if self.voice is not None:
            return
        await self.open_thread(event.threadId)
        change = SetVoiceArgs(model=event.model, voice=event.voice, effort=event.effort)
        picked = resolve_selection(self.access.models(), change, self.selection)
        if isinstance(picked, Selection):
            self.selection = picked
        provider = self.model().provider
        if not await self.access.prepare(provider):
            await self.set_state("idle", f"{provider_label(provider)} is not connected: add a key in Providers")
            return
        if not await self.open_voice():
            await self.set_state("idle", "could not reach the voice model")
            return
        await self.set_state("listening")
        await self.emit(VoiceConfigured(selection=self.selection, source="client"))

    def model(self) -> VoiceModel:
        models = self.access.models()
        return find_model(models, self.selection.model) or models[0]

    def voice_config(self) -> VoiceConfig:
        return VoiceConfig(
            voice=self.selection.voice,
            instructions=self.instructions,
            tools=tuple(tool.get_schema() for tool in self.tools.values()),
            reasoning_effort=self.selection.effort,
        )

    async def open_voice(self) -> bool:
        """Open a voice session on the selected model, seed it with the thread so far, and read it."""
        model = self.model()
        if not await self.access.prepare(model.provider):
            return False
        try:
            voice = await self.access.open_voice(model, self.voice_config())
            await self.replay(voice)
        except (OSError, WebSocketException) as exc:
            log.error("could not open the voice session on %s: %s", model.id, exc)
            return False
        self.voice = voice
        log.info("voice session open (model %s)", model.id)
        self.spawn(self.pump(voice))
        self.spawn(self.answer(voice))
        return True

    async def replay(self, voice: VoiceSession) -> None:
        if self.thread is None:
            return
        for message in self.storage.messages(self.thread.id):
            await voice.add_message("assistant" if message.role == "assistant" else "user", message.text)

    async def swap(self) -> bool:
        """Move onto a newly selected model: the model is fixed per voice session, so open a new
        one (seeded from the thread). The browser socket stays."""
        old = self.voice
        if not await self.open_voice():
            return False
        if old is not None:
            await old.aclose()
        return True

    async def close_voice(self) -> None:
        self.desktop.configure(False)
        voice, self.voice = self.voice, None
        self.idle.set()
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
            # Barge-in: stop the speech (never an agent run) and tell the client to flush its
            # playback queue now (a plain `listening` at the end of a turn must not cut audio off).
            self.barge_ins += 1
            await voice.cancel()
            await self.set_state("listening", BARGE_IN)
        elif isinstance(event, SpeechStopped):
            self.idle.clear()
            await self.set_state("thinking")
        elif isinstance(event, UserTranscript):
            if event.text:
                self.record("user", event.text)
                await self.emit(TranscriptDelta(role="user", text=event.text))
                await self.title_from(event.text)
            else:
                # Noise, not words: drop whatever the model started saying about it.
                await voice.cancel()
                await self.turn_over()
        elif isinstance(event, AudioDelta):
            await self.set_state("speaking")
            try:
                await self.browser.send_bytes(event.audio)
            except (WebSocketDisconnect, RuntimeError):
                log.info("browser gone while sending audio")
        elif isinstance(event, VoiceError):
            log.error("voice model error: %s", event.message)

    # ------------------------------------------------------------ answers and tools

    async def answer(self, voice: VoiceSession) -> None:
        """Read the session's responses in order for as long as it is open: show and store each
        answer, and run the tools it calls, asking for the next answer with their results."""
        while self.voice is voice:
            response = await self.read_response(voice)
            if response.done is None or self.voice is not voice:
                return
            if response.text:
                self.record("assistant", "".join(response.text))
            if response.calls and response.done.error is None:
                await self.run_tools(voice, response.calls)
            else:
                await self.turn_over()

    async def read_response(self, voice: VoiceSession) -> Response:
        response = Response()
        async for event in voice.response():
            if isinstance(event, TextDelta):
                response.text.append(event.text)
                await self.emit(TranscriptDelta(role="assistant", text=event.text))
            elif isinstance(event, ToolCall):
                response.calls.append(event)
            else:
                response.done = event
        return response

    async def run_tools(self, voice: VoiceSession, calls: list[ToolCall]) -> None:
        self.idle.clear()
        barge_ins = self.barge_ins
        for call in calls:
            await voice.add_tool_result(call.call_id, await self.run_tool(call))
        if self.barge_ins == barge_ins:
            await voice.respond()
        # Otherwise the user spoke meanwhile: the answer server VAD starts for that sees the results.

    async def run_tool(self, call: ToolCall) -> str:
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
        token = RUN_DESKTOP.set(self.desktop)
        try:
            return tool_output(await tool.execute(sink=self.sink, args=args))
        except Exception as exc:  # a tool failure is the model's to explain, as in a harness run
            log.warning("voice tool %s failed: %s", call.name, exc)
            return json.dumps({"error": str(exc) or type(exc).__name__})
        finally:
            RUN_DESKTOP.reset(token)

    async def turn_over(self) -> None:
        """No answer is under way: speak a queued phase notice, or apply a deferred model switch."""
        self.idle.set()
        if self.swap_after_turn:
            self.swap_after_turn = False
            await self.apply_swap("tool")
        notices, self.idle_notices = list(self.idle_notices.values()), {}
        if notices and self.voice is not None:
            for notice in notices:
                await self.voice.add_message("user", notice.text)
            await self.ask(self.voice)
            return
        await self.set_state("listening")

    async def ask(self, voice: VoiceSession) -> None:
        self.idle.clear()
        await voice.respond()

    async def interrupt(self) -> None:
        if self.voice is None:
            return
        self.barge_ins += 1
        await self.voice.cancel()
        await self.set_state("listening", BARGE_IN)

    async def widget_action(self, action: WidgetAction) -> None:
        widget = self.board.get(action.id)
        if self.voice is None or widget is None:
            return
        text = action_text(action, widget)
        self.barge_ins += 1
        await self.voice.cancel()
        self.record("user", text)
        await self.set_state("thinking")
        await self.voice.add_message("user", text)
        await self.ask(self.voice)

    # ------------------------------------------------------------ agent runs (AgentObserver)

    async def show(self, event: ServerEvent) -> None:
        await self.emit(event)

    async def tell(self, notice: Notice) -> None:
        """Relay an agent notice: `interrupt` now (cutting off any answer), `when_idle` once no
        answer is under way (only a run's latest phase), `silent` into the conversation only."""
        if notice.tag != "when_idle":
            self.record("agent", notice.text)
        voice = self.voice
        if voice is None:
            return
        if notice.tag == "when_idle" and not self.idle.is_set():
            self.idle_notices[notice.run_id] = notice
            return
        if notice.tag == "interrupt" and not self.idle.is_set():
            self.barge_ins += 1
            await voice.cancel()
            await self.emit(StateEvent(state=self.state, detail=BARGE_IN))
        await voice.add_message("user", notice.text)
        if notice.tag != "silent":
            await self.ask(voice)

    # ------------------------------------------------------------ model, voice and effort

    async def configure_from_client(self, change: SetVoiceArgs) -> None:
        """A picker commit. A model switch waits for the current answer to finish (never
        cutting it off), then swaps and tells the browser the session is listening."""
        async with self.configure_lock:
            swapping = self.voice is not None and (change.model or self.selection.model) != self.selection.model
            if swapping:
                try:
                    async with asyncio.timeout(SWAP_WAIT_S):
                        await self.idle.wait()
                except TimeoutError:
                    log.warning("model switch waited %.0fs for the answer to end; switching anyway", SWAP_WAIT_S)
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
        elif source == "tool":
            # The answer that asked is still speaking its confirmation on this session.
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

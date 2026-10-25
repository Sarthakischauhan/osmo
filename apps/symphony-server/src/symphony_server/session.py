"""One Osmo session: bridges the browser WebSocket and the Grok realtime WebSocket.

Symphony owns the upstream connection and the API key, runs tools between the
two, validates widgets, and translates events both ways. The browser only
ever sees the Osmo wire protocol (protocol.py).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from core_harness import Tool
from pydantic import ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from symphony_server import grok_events as grok
from symphony_server.protocol import (
    Interrupt,
    ModelsEvent,
    OsmoState,
    RenderedWidget,
    ServerEvent,
    SessionConfigure,
    SessionEnd,
    SessionStart,
    StateEvent,
    TranscriptDelta,
    VoiceConfigured,
    VoicePreviewRequest,
    VoicePreviewResult,
    WidgetAction,
    WidgetRender,
    WidgetUpdate,
    client_event_adapter,
)
from symphony_server.settings import Settings
from symphony_server.voices import (
    SET_VOICE,
    SET_VOICE_PARAMETERS,
    VOICE_MODELS,
    PREVIEW_RATE_LIMIT,
    PREVIEW_RATE_WINDOW_S,
    PREVIEW_SECONDS,
    PreviewCache,
    RateLimiter,
    preview_error,
    default_selection,
    Selection,
    realtime_url_for,
    resolve_selection,
    set_voice_description,
)
from symphony_server.widgets import (
    RENDER_WIDGET_PARAMETERS,
    UPDATE_WIDGET_PARAMETERS,
    OptionsWidget,
    Widget,
    describe_widget,
    widget_adapter,
    widget_props,
)

log = logging.getLogger("symphony.session")

RENDER_WIDGET = "render_widget"
UPDATE_WIDGET = "update_widget"
# `state` detail telling the client to drop queued assistant audio immediately.
BARGE_IN = "barge-in"
# Only the most recent spoken turns are replayed into a hot-swapped upstream.
MAX_REPLAY_ITEMS = 40
# A picker model switch waits this long for an in-flight response or tool call to finish.
SWAP_WAIT_S = 15.0
# Voice previews are played only while the assistant is quiet.
PREVIEW_STATES: tuple[OsmoState, ...] = ("idle", "listening")
# A model that writes its tool call as text instead of emitting a function call item.
TOOL_CALL_TAG = "<tool_call>"


def realtime_tools(tools: Mapping[str, Tool]) -> list[dict[str, Any]]:
    """Session tool list: Grok's server-side web search plus Symphony's functions."""
    functions = [{"type": "function", **tool.get_schema()} for tool in tools.values()]
    return [
        {"type": "web_search"},
        {
            "type": "function",
            "name": RENDER_WIDGET,
            "description": "Show a widget on the user's canvas. Returns what is now shown.",
            "parameters": RENDER_WIDGET_PARAMETERS,
        },
        {
            "type": "function",
            "name": UPDATE_WIDGET,
            "description": "Change props of a widget already on the canvas.",
            "parameters": UPDATE_WIDGET_PARAMETERS,
        },
        {
            "type": "function",
            "name": SET_VOICE,
            "description": set_voice_description(VOICE_MODELS),
            "parameters": SET_VOICE_PARAMETERS,
        },
        *functions,
    ]


@dataclass
class TranscriptGuard:
    """Per assistant item: passes spoken text through and cuts everything from a `<tool_call>` tag on.

    A tail that could be the start of the tag is held back until the next delta decides it."""

    held: str = ""
    leaked: bool = False

    def feed(self, delta: str) -> str:
        if self.leaked:
            return ""
        text = self.held + delta
        cut = text.find(TOOL_CALL_TAG)
        if cut >= 0:
            self.leaked, self.held = True, ""
            return text[:cut]
        keep = next((n for n in range(len(TOOL_CALL_TAG) - 1, 0, -1) if text.endswith(TOOL_CALL_TAG[:n])), 0)
        self.held = text[len(text) - keep :] if keep else ""
        return text[: len(text) - keep]


@dataclass
class HistoryItem:
    role: Literal["user", "assistant"]
    text: str
    item_id: str | None = None


class OsmoSession:
    def __init__(
        self, browser: WebSocket, settings: Settings, tools: Mapping[str, Tool], previews: PreviewCache
    ) -> None:
        self.browser = browser
        self.previews = previews
        self.preview_tasks: set[asyncio.Task[None]] = set()
        self.selection = default_selection(VOICE_MODELS, settings.voice)
        self.instructions = settings.instructions
        self.settings = settings
        self.tools = tools
        self.upstream: ClientConnection | None = None
        self.pump: asyncio.Task[None] | None = None
        self.state: OsmoState = "idle"
        self.state_detail: str | None = None
        self.widgets: dict[str, Widget] = {}
        self.widget_count = 0
        # Assistant audio is dropped from barge-in until the next response starts.
        self.muted = False
        self.response_active = False
        self.outputs_pending = False
        # Spoken turns so far, replayed into a fresh upstream when the model is hot-swapped.
        self.history: list[HistoryItem] = []
        self.transcript_guards: dict[str | None, TranscriptGuard] = {}
        # Function calls already run this response: xAI may report one call by two events.
        self.handled_calls: set[str] = set()
        self.upstream_count = 0
        # Set while no response or tool call is in flight; a picker model switch waits for it.
        self.turn_idle = asyncio.Event()
        self.turn_idle.set()
        self.configure_lock = asyncio.Lock()
        self.client_tasks: set[asyncio.Task[None]] = set()
        self.preview_limiter = RateLimiter(PREVIEW_RATE_LIMIT, PREVIEW_RATE_WINDOW_S)
        # Mic audio is not forwarded until this monotonic time, so a preview playing on the
        # speakers is not heard as the user talking.
        self.mic_paused_until = 0.0

    # ------------------------------------------------------------ browser side

    async def run(self) -> None:
        await self.emit(ModelsEvent(models=list(VOICE_MODELS), selection=self.selection))
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
            for task in (*self.preview_tasks, *self.client_tasks):
                task.cancel()
            await self.close_upstream()

    async def forward_audio(self, pcm: bytes) -> None:
        if self.upstream is None or time.monotonic() < self.mic_paused_until:
            return
        try:
            await self.upstream.send(pcm)
        except ConnectionClosed:
            await self.upstream_lost()

    async def handle_client_text(self, text: str) -> None:
        try:
            event = client_event_adapter.validate_json(text)
        except ValidationError as exc:
            log.warning("bad client event: %s", exc.errors()[:1])
            return
        if isinstance(event, SessionStart):
            await self.start(event)
        elif isinstance(event, SessionEnd):
            await self.close_upstream()
            await self.set_state("idle")
        elif isinstance(event, Interrupt):
            await self.interrupt()
        elif isinstance(event, WidgetAction):
            await self.widget_action(event)
        elif isinstance(event, SessionConfigure):
            # Runs beside the receive loop: a model switch may wait for the current turn to end.
            change = event.model_dump(exclude={"type"}, exclude_none=True)
            task = asyncio.create_task(self.configure_from_client(change))
            self.client_tasks.add(task)
            task.add_done_callback(self.client_tasks.discard)
        elif isinstance(event, VoicePreviewRequest):
            task = asyncio.create_task(self.preview(event))
            self.preview_tasks.add(task)
            task.add_done_callback(self.preview_tasks.discard)

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

    # ------------------------------------------------------------ upstream side

    async def send_upstream(self, event: grok.GrokClientEvent, target: ClientConnection | None = None) -> None:
        upstream = target or self.upstream
        if upstream is None:
            return
        try:
            await upstream.send(event.model_dump_json(exclude_none=True))
        except ConnectionClosed:
            if upstream is self.upstream:
                await self.upstream_lost()

    async def open_upstream(self, model: str) -> ClientConnection | None:
        try:
            upstream = await connect(
                realtime_url_for(self.settings.realtime_url, model),
                additional_headers={"Authorization": f"Bearer {self.settings.xai_api_key}"},
                max_size=None,
            )
        except (OSError, ConnectionClosed) as exc:
            log.error("could not reach Grok realtime: %s", exc)
            return None
        self.upstream_count += 1
        log.info("upstream #%d open (model %s)", self.upstream_count, model)
        return upstream

    async def start(self, event: SessionStart) -> None:
        if self.upstream is not None:
            return
        if not self.settings.xai_api_key:
            await self.set_state("idle", "XAI_API_KEY is not configured on the server")
            return
        picked = resolve_selection(
            VOICE_MODELS, event.model_dump(include={"model", "voice", "effort"}, exclude_none=True), self.selection
        )
        if isinstance(picked, Selection):
            self.selection = picked
        self.upstream = await self.open_upstream(self.selection.model)
        if self.upstream is None:
            await self.set_state("idle", "could not reach the voice model")
            return
        self.instructions = event.instructions or self.settings.instructions
        await self.send_session_update()
        self.pump = asyncio.create_task(self.pump_upstream(self.upstream))
        await self.set_state("listening")
        await self.emit(VoiceConfigured(selection=self.selection, source="client"))

    async def send_session_update(self, target: ClientConnection | None = None) -> None:
        """Voice and reasoning effort go through `session.update`, which xAI accepts mid-session."""
        effort = self.selection.effort
        await self.send_upstream(
            grok.SessionUpdate(
                session=grok.SessionConfig(
                    voice=self.selection.voice,
                    instructions=self.instructions,
                    tools=realtime_tools(self.tools),
                    reasoning=None if effort is None else grok.Reasoning(effort=effort),
                )
            ),
            target,
        )

    async def hot_swap(self) -> bool:
        """Move the live conversation onto a new realtime model without dropping the browser.

        The model is fixed per upstream connection (`?model=`), so open a second
        connection, configure it, replay the spoken turns, switch audio over and only
        then close the old one. The browser WebSocket is never touched.
        """
        old, old_pump = self.upstream, self.pump
        old_number = self.upstream_count
        fresh = await self.open_upstream(self.selection.model)
        if fresh is None:
            return False
        await self.send_session_update(fresh)
        carried = [item for item in self.history if item.text.strip()][-MAX_REPLAY_ITEMS:]
        for item in carried:
            await self.send_upstream(grok.ConversationItemCreate(item=history_item(item)), fresh)
        self.upstream = fresh
        self.pump = asyncio.create_task(self.pump_upstream(fresh))
        self.response_active = False
        self.outputs_pending = False
        self.turn_idle.set()
        log.info(
            "hot-swap upstream #%d -> #%d (model %s): carried %d items",
            old_number,
            self.upstream_count,
            self.selection.model,
            len(carried),
        )
        if old is not None:
            await old.close()
            log.info("upstream #%d closed after hot-swap", old_number)
        # A `set_voice` call runs inside the old pump; it ends on its own once the socket is closed.
        if old_pump is not None and old_pump is not asyncio.current_task():
            old_pump.cancel()
        return True

    async def configure_from_client(self, change: Mapping[str, Any]) -> None:
        """A picker commit. A model switch waits for the in-flight response or tool call to finish
        (never cancelling it), then swaps and tells the browser the session is listening."""
        async with self.configure_lock:
            swapping = self.upstream is not None and change.get("model", self.selection.model) != self.selection.model
            if swapping:
                try:
                    async with asyncio.timeout(SWAP_WAIT_S):
                        await self.turn_idle.wait()
                except TimeoutError:
                    log.warning("model switch waited %.0fs for the turn to end; switching anyway", SWAP_WAIT_S)
            before = self.upstream
            outcome = await self.configure(change, "client")
            if isinstance(outcome, Selection) and self.upstream is not before:
                self.muted = False
                await self.set_state("listening")

    async def configure(self, change: Mapping[str, Any], source: Literal["client", "tool"]) -> Selection | str:
        """The one path for picker commits and `set_voice`: validate, apply upstream, tell the browser.

        The browser always hears back: `voice.configured` with the applied selection, or with the
        selection still in effect plus `error` when the change was rejected or failed."""
        picked = resolve_selection(VOICE_MODELS, change, self.selection)
        if isinstance(picked, str):
            log.warning("rejected voice change from %s: %s", source, picked)
            await self.emit(VoiceConfigured(selection=self.selection, source=source, error=picked))
            return picked
        previous = self.selection
        self.selection = picked
        if self.upstream is not None:
            if picked.model != previous.model:
                if not await self.hot_swap():
                    self.selection = previous
                    error = f"could not open {picked.model}; still on {previous.model}"
                    await self.emit(VoiceConfigured(selection=previous, source=source, error=error))
                    return error
            else:
                await self.send_session_update()
        await self.emit(VoiceConfigured(selection=picked, source=source))
        return picked

    async def preview(self, request: VoicePreviewRequest) -> None:
        refused = preview_error(VOICE_MODELS, request.model, request.voice)
        if refused is None and self.state not in PREVIEW_STATES:
            refused = f"previews play only while idle or listening (now {self.state})"
        if refused is None and not self.preview_limiter.allow():
            refused = "too many previews; try again in a few seconds"
        if refused is not None:
            await self.emit(
                VoicePreviewResult(requestId=request.requestId, model=request.model, voice=request.voice, error=refused)
            )
            return
        try:
            audio = await self.previews.get(request.model, request.voice)
        except Exception as exc:  # a failed preview is reported to the pill, never fatal to the session
            log.warning("voice preview %s/%s failed: %s", request.model, request.voice, exc)
            await self.emit(
                VoicePreviewResult(requestId=request.requestId, model=request.model, voice=request.voice, error=str(exc))
            )
            return
        await self.emit(
            VoicePreviewResult(
                requestId=request.requestId,
                model=request.model,
                voice=request.voice,
                audio=base64.b64encode(audio).decode("ascii"),
            )
        )
        # The browser plays it now; keep its speakers out of the mic for that long.
        self.mic_paused_until = time.monotonic() + PREVIEW_SECONDS

    async def close_upstream(self) -> None:
        upstream, self.upstream = self.upstream, None
        pump, self.pump = self.pump, None
        if upstream is not None:
            await upstream.close()
        if pump is not None and pump is not asyncio.current_task():
            pump.cancel()
        self.response_active = False
        self.outputs_pending = False
        self.turn_idle.set()

    async def upstream_lost(self) -> None:
        await self.close_upstream()
        await self.set_state("idle", "voice session ended")

    async def pump_upstream(self, upstream: ClientConnection) -> None:
        try:
            async for message in upstream:
                if isinstance(message, bytes):
                    await self.assistant_audio(message)
                else:
                    await self.handle_grok_text(message)
        except ConnectionClosed:
            pass
        if self.upstream is upstream:
            await self.upstream_lost()

    async def assistant_audio(self, pcm: bytes) -> None:
        if self.muted:
            return
        await self.set_state("speaking")
        try:
            await self.browser.send_bytes(pcm)
        except (WebSocketDisconnect, RuntimeError):
            log.info("browser gone while sending audio")

    async def handle_grok_text(self, text: str) -> None:
        try:
            envelope = grok.EventEnvelope.model_validate_json(text)
        except ValidationError:
            log.warning("unparseable upstream frame")
            return
        if envelope.type not in grok.HANDLED_SERVER_EVENTS:
            log.debug("upstream %s", envelope.type)
            return
        try:
            event = grok.grok_server_event_adapter.validate_json(text)
        except ValidationError as exc:
            log.warning("malformed upstream %s: %s", envelope.type, exc.errors()[:1])
            return
        await self.handle_grok_event(event)

    async def handle_grok_event(self, event: grok.GrokServerEvent) -> None:
        if isinstance(event, grok.SpeechStarted):
            # Barge-in: stop forwarding assistant audio now and tell the client
            # to flush its playback queue (a plain `listening` after response.done
            # must not cut off audio that is still playing).
            self.muted = True
            await self.set_state("listening", BARGE_IN)
        elif isinstance(event, grok.SpeechStopped):
            await self.set_state("thinking")
        elif isinstance(event, grok.InputTranscriptionCompleted):
            if event.transcript.strip():
                self.history.append(HistoryItem(role="user", text=event.transcript, item_id=event.item_id))
                await self.emit(TranscriptDelta(role="user", text=event.transcript, itemId=event.item_id))
        elif isinstance(event, grok.ResponseCreated):
            self.muted = False
            self.response_active = True
            self.handled_calls.clear()
            self.turn_idle.clear()
            if self.state != "working":
                await self.set_state("thinking")
        elif isinstance(event, grok.OutputAudioTranscriptDelta):
            if not self.muted:
                await self.assistant_transcript(event.item_id, event.delta)
        elif isinstance(event, grok.OutputAudioDelta):
            await self.assistant_audio(base64.b64decode(event.delta))
        elif isinstance(event, grok.FunctionCallArgumentsDone):
            await self.run_function(event)
        elif isinstance(event, grok.OutputItemDone):
            item = event.item
            if item.type == "function_call" and item.call_id and item.name:
                await self.run_function(
                    grok.FunctionCallArgumentsDone(
                        type="response.function_call_arguments.done",
                        call_id=item.call_id,
                        name=item.name,
                        arguments=item.arguments or "{}",
                    )
                )
        elif isinstance(event, grok.ResponseDone):
            self.response_active = False
            guards, self.transcript_guards = self.transcript_guards, {}
            for item_id, guard in guards.items():
                if guard.held and not self.muted:
                    await self.show_assistant_text(item_id, guard.held)
            if self.outputs_pending:
                # All function outputs of this response are in; continue once.
                self.outputs_pending = False
                await self.send_upstream(grok.ResponseCreate())
                await self.set_state("thinking")
            else:
                self.turn_idle.set()
                await self.set_state("listening")
        elif isinstance(event, grok.ErrorEvent):
            log.error("grok error: %s", event.error.message)
        elif isinstance(event, grok.SessionUpdated):
            log.info("grok session configured")

    # ------------------------------------------------------------ tools and widgets

    async def run_function(self, call: grok.FunctionCallArgumentsDone) -> None:
        if call.call_id in self.handled_calls:
            return
        self.handled_calls.add(call.call_id)
        try:
            parsed = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            result: dict[str, Any] = {"error": "arguments must be a JSON object"}
        elif call.name == RENDER_WIDGET:
            result = await self.render_widget(parsed)
        elif call.name == UPDATE_WIDGET:
            result = await self.update_widget(parsed)
        elif call.name == SET_VOICE:
            before = self.upstream
            outcome = await self.configure(parsed, "tool")
            if self.upstream is not before:
                # Hot-swapped: the call belongs to the closed session. The new model sees the
                # user's request in the carried history and confirms the switch itself.
                await self.send_upstream(grok.ResponseCreate())
                return
            result = (
                {"error": outcome}
                if isinstance(outcome, str)
                else {"configured": outcome.model_dump(exclude_none=True), "say": "Confirm the switch in one short line."}
            )
        elif call.name in self.tools:
            await self.set_state("working", activity_label(call.name, parsed))
            try:
                result = {"result": await self.tools[call.name].execute(sink=None, args=parsed)}
            except Exception as exc:  # tool failures go back to the model, never crash the session
                log.warning("tool %s failed: %s", call.name, exc)
                result = {"error": f"{call.name} failed: {exc}"}
        else:
            result = {"error": f"unknown tool {call.name}"}
        await self.send_upstream(
            grok.ConversationItemCreate(
                item=grok.FunctionCallOutputItem(call_id=call.call_id, output=json.dumps(result))
            )
        )
        self.outputs_pending = True

    async def render_widget(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            widget = widget_adapter.validate_python({"type": args.get("type"), "props": args.get("props")})
        except ValidationError as exc:
            return {"shown": None, "error": f"invalid widget, nothing shown: {compact_errors(exc)}"}
        self.widget_count += 1
        # Ids must be unique across sessions too: the browser keeps earlier widgets on screen when a
        # new session starts, so a per-session counter alone would collide with them.
        widget_id = f"w{self.widget_count}-{secrets.token_hex(4)}"
        self.widgets[widget_id] = widget
        await self.emit(
            WidgetRender(widget=RenderedWidget(id=widget_id, type=widget.type, props=widget_props(widget)))
        )
        return {"shown": describe_widget(widget), "id": widget_id}

    async def update_widget(self, args: dict[str, Any]) -> dict[str, Any]:
        widget_id = str(args.get("id", ""))
        patch = args.get("patch")
        current = self.widgets.get(widget_id)
        if current is None or not isinstance(patch, dict):
            return {"error": f"no widget {widget_id!r} on screen or patch is not an object"}
        merged = {**widget_props(current), **patch}
        try:
            widget = widget_adapter.validate_python({"type": current.type, "props": merged})
        except ValidationError as exc:
            return {"error": f"invalid patch, widget unchanged: {compact_errors(exc)}"}
        self.widgets[widget_id] = widget
        props = widget_props(widget)
        await self.emit(WidgetUpdate(id=widget_id, patch={key: props.get(key) for key in patch}))
        return {"shown": f"updated {describe_widget(widget)}", "id": widget_id}

    async def widget_action(self, action: WidgetAction) -> None:
        if self.upstream is None:
            return
        widget = self.widgets.get(action.id)
        if widget is None:
            return
        await self.interrupt_response()
        text = action_text(action, widget)
        self.history.append(HistoryItem(role="user", text=text))
        await self.send_upstream(
            grok.ConversationItemCreate(item=grok.UserMessageItem(content=[grok.InputText(text=text)]))
        )
        await self.send_upstream(grok.ResponseCreate())
        await self.set_state("thinking")

    async def assistant_transcript(self, item_id: str | None, delta: str) -> None:
        """Shows and remembers what the assistant says, minus any tool call it wrote as text.

        Leaked tool-call text never reaches the browser or the history replayed on a hot-swap,
        where it would teach the next model to write tags instead of calling tools."""
        guard = self.transcript_guards.setdefault(item_id, TranscriptGuard())
        was_leaked = guard.leaked
        visible = guard.feed(delta)
        if guard.leaked and not was_leaked:
            log.warning("model wrote a tool call as text in item %s; dropped from the transcript", item_id)
        if visible:
            await self.show_assistant_text(item_id, visible)

    async def show_assistant_text(self, item_id: str | None, text: str) -> None:
        self.remember_assistant(item_id, text)
        await self.emit(TranscriptDelta(role="assistant", text=text, itemId=item_id))

    def remember_assistant(self, item_id: str | None, delta: str) -> None:
        last = self.history[-1] if self.history else None
        if last is not None and last.role == "assistant" and last.item_id == item_id:
            last.text += delta
        else:
            self.history.append(HistoryItem(role="assistant", text=delta, item_id=item_id))

    async def interrupt_response(self) -> None:
        self.muted = True
        if self.response_active:
            self.response_active = False
            self.outputs_pending = False
            await self.send_upstream(grok.ResponseCancel())

    async def interrupt(self) -> None:
        await self.interrupt_response()
        if self.upstream is not None:
            await self.set_state("listening", BARGE_IN)


def history_item(item: HistoryItem) -> grok.UserMessageItem | grok.AssistantMessageItem:
    if item.role == "user":
        return grok.UserMessageItem(content=[grok.InputText(text=item.text)])
    return grok.AssistantMessageItem(content=[grok.OutputText(text=item.text)])


def action_text(action: WidgetAction, widget: Widget) -> str:
    """A widget tap, phrased as the user turn it stands in for."""
    picked = action.value or ""
    if isinstance(widget, OptionsWidget):
        for index, choice in enumerate(widget.props.choices):
            if choice.id == picked:
                picked = f"option {index + 1}, {choice.label}"
    detail = f" -> {picked}" if picked else ""
    return f"[Tapped on screen] {widget.type} widget {action.id}: {action.action}{detail}"


def activity_label(name: str, args: Mapping[str, Any]) -> str:
    if name == "get_weather":
        return f"Checking weather · {args.get('location', '')}"
    if name == "get_stock_history":
        return f"Fetching prices · {str(args.get('symbol', '')).upper()}"
    return f"Running {name}"


def compact_errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()[:5]
    )

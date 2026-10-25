from __future__ import annotations

import json
import re
from typing import Any

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from conftest import FakeGrok, make_client
from symphony_server.settings import Settings

WEATHER_CARD = {
    "type": "card",
    "props": {"title": "Palo Alto weekend", "body": "**72°** Sat · sunny\n\n**68°** Sun · partly cloudy"},
}
DINNER = {
    "type": "options",
    "props": {
        "prompt": "Pick a dinner spot",
        "choices": [
            {"id": "italian", "label": "Italian, wood-fired", "detail": "0.4 mi"},
            {"id": "ramen", "label": "Ramen counter", "detail": "0.8 mi"},
        ],
    },
}


def next_event(ws: WebSocketTestSession, event_type: str) -> dict[str, Any]:
    while True:
        message = ws.receive()
        text = message.get("text")
        if text is None:
            continue
        event: dict[str, Any] = json.loads(text)
        if event["type"] == event_type:
            return event


def start(ws: WebSocketTestSession, grok: FakeGrok) -> dict[str, Any]:
    ws.send_text(json.dumps({"type": "session.start", "voice": "eve"}))
    assert next_event(ws, "state")["state"] == "listening"
    # session.start reports the pick it applied.
    assert next_event(ws, "voice.configured")["selection"]["voice"] == "eve"
    return grok.next_of("session.update")


def say(grok: FakeGrok, text: str) -> None:
    """The user finishes an utterance; the bridge runs it as a harness turn. With server VAD the
    session already heard it and is answering, so the turn's model call sends nothing upstream."""
    grok.send({"type": "input_audio_buffer.speech_stopped"})
    grok.send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u", "transcript": text})


def asked(grok: FakeGrok) -> str:
    """A typed turn's model call (a widget tap, or history replayed on a fresh session): the
    user message it adds, then the response it asks for."""
    item = grok.next_of("conversation.item.create")["item"]
    grok.next_of("response.create")
    text: str = item["content"][0]["text"]
    return text


def call(grok: FakeGrok, name: str, args: dict[str, Any], call_id: str, response_id: str = "r1") -> None:
    grok.send({"type": "response.created", "response": {"id": response_id}})
    grok.send(
        {
            "type": "response.function_call_arguments.done",
            "response_id": response_id,
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(args),
        }
    )
    grok.send({"type": "response.done", "response": {"id": response_id}})


def tool_output(grok: FakeGrok) -> dict[str, Any]:
    """The harness ran the tool and hands its result back to the voice session for the next response."""
    item = grok.next_of("conversation.item.create")["item"]
    assert item["type"] == "function_call_output"
    grok.next_of("response.create")
    output: dict[str, Any] = json.loads(item["output"])
    return output


def answer(grok: FakeGrok, text: str, response_id: str) -> None:
    grok.send({"type": "response.created", "response": {"id": response_id}})
    grok.send({"type": "response.output_audio_transcript.delta", "response_id": response_id, "delta": text})
    grok.send({"type": "response.done", "response": {"id": response_id}})


def render(ws: WebSocketTestSession, grok: FakeGrok, widget: dict[str, Any], call_id: str) -> str:
    """One full turn that renders `widget`; returns its id once the turn is over."""
    say(grok, "show me")
    call(grok, "render_widget", widget, call_id, f"{call_id}-a")
    widget_id: str = next_event(ws, "widget.render")["widget"]["id"]
    assert tool_output(grok)["id"] == widget_id
    answer(grok, "Done.", f"{call_id}-b")
    wait_listening(ws)
    return widget_id


def wait_listening(ws: WebSocketTestSession) -> None:
    while next_event(ws, "state")["state"] != "listening":
        pass


def test_session_start_configures_grok_server_side(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        update = start(ws, grok)
        assert grok.headers.get(timeout=5) == "Bearer test-key"
        assert grok.paths.get(timeout=5) == "/v1/realtime?model=grok-voice-latest"
        session = update["session"]
        assert session["voice"] == "eve"
        assert session["turn_detection"] == {"type": "server_vad"}
        names = {tool["name"] for tool in session["tools"]}
        assert names == {"web_search", "create_image", "render_widget", "update_widget", "set_voice", "get_weather", "get_stock_history"}
        assert all(tool["type"] == "function" for tool in session["tools"])

        ws.send_bytes(b"\x01\x00\x02\x00")
        assert grok.next_bytes() == b"\x01\x00\x02\x00"


def test_a_spoken_turn_runs_the_harness_and_its_tool_renders_a_widget(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "Weather this weekend?")
        assert next_event(ws, "transcript.delta") == {"type": "transcript.delta", "role": "user", "text": "Weather this weekend?"}

        call(grok, "render_widget", WEATHER_CARD, "c1")
        assert next_event(ws, "state") == {"type": "state", "state": "working", "detail": "Running render_widget"}
        rendered = next_event(ws, "widget.render")["widget"]
        assert re.fullmatch(r"w\d+-[0-9a-f]{8}", rendered["id"])
        assert rendered == {"id": rendered["id"], "type": "card", "props": WEATHER_CARD["props"]}
        assert tool_output(grok) == {"shown": 'card "Palo Alto weekend"', "id": rendered["id"]}

        answer(grok, "Sunny, 72.", "r2")
        assert next_event(ws, "transcript.delta") == {"type": "transcript.delta", "role": "assistant", "text": "Sunny, 72."}
        wait_listening(ws)


def test_a_model_switch_replays_the_harness_history(client: TestClient, grok: FakeGrok) -> None:
    """History lives in the harness (keyed by the bridge's session id): a new voice session on
    another model is seeded from it on its first turn."""
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        widget_id = render(ws, grok, DINNER, "h1")
        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "eve"}))
        assert next_event(ws, "voice.configured")["selection"]["model"] == "grok-voice-3.0"
        assert grok.closed.get(timeout=5) == "/v1/realtime?model=grok-voice-latest"
        grok.next_of("session.update")

        ws.send_text(json.dumps({"type": "widget.action", "id": widget_id, "action": "select", "value": "ramen"}))
        replayed = [grok.next_of("conversation.item.create")["item"] for _ in range(3)]
        assert [(item["role"], item["content"][0]["text"]) for item in replayed] == [
            ("user", "show me"),
            ("assistant", "Done."),
            ("user", f"[Tapped on screen] options widget {widget_id}: select -> option 2, Ramen counter"),
        ]


def test_invalid_widget_never_reaches_canvas(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "chart it")
        call(grok, "render_widget", {"type": "chart", "props": {"kind": "pie", "series": []}}, "c2")
        result = tool_output(grok)
        assert result["shown"] is None and "invalid widget" in result["error"]
        answer(grok, "Sorry.", "r2")
        while True:
            event = json.loads(ws.receive_text())
            assert event["type"] != "widget.render"
            if event == {"type": "state", "state": "listening"}:
                break


def test_widget_action_becomes_a_user_turn(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        widget_id = render(ws, grok, DINNER, "c3")
        ws.send_text(json.dumps({"type": "widget.action", "id": widget_id, "action": "select", "value": "ramen"}))
        assert asked(grok) == f"[Tapped on screen] options widget {widget_id}: select -> option 2, Ramen counter"


def test_update_touches_only_the_targeted_widget(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        first = render(ws, grok, WEATHER_CARD, "c6")
        second = render(ws, grok, WEATHER_CARD, "c7")
        assert first != second

        say(grok, "rename it")
        call(grok, "update_widget", {"id": second, "patch": {"title": "Only the new one"}}, "c8")
        assert next_event(ws, "widget.update") == {"type": "widget.update", "id": second, "patch": {"title": "Only the new one"}}
        assert tool_output(grok)["id"] == second


def test_widget_ids_never_repeat_across_sessions(client: TestClient, grok: FakeGrok) -> None:
    """The browser keeps earlier widgets on screen after End + restart, so a new session must not reuse their ids."""
    ids = []
    for call_id in ("s1", "s2"):
        with client.websocket_connect("/ws") as ws:
            start(ws, grok)
            ids.append(render(ws, grok, WEATHER_CARD, call_id))
    assert ids[0] != ids[1]


def test_update_for_an_unknown_id_is_rejected(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "rename it")
        call(grok, "update_widget", {"id": "w1", "patch": {"title": "x"}}, "c9")
        assert "no widget 'w1'" in tool_output(grok)["error"]


def test_barge_in_cancels_the_turn_and_drops_its_late_audio(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "tell me a story")
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send(b"\x10\x00" * 4)
        assert next_event(ws, "state")["state"] == "thinking"
        assert next_event(ws, "state")["state"] == "speaking"
        assert ws.receive_bytes() == b"\x10\x00" * 4

        grok.send({"type": "input_audio_buffer.speech_started", "item_id": "u1"})
        assert next_event(ws, "state") == {"type": "state", "state": "listening", "detail": "barge-in"}
        grok.next_of("response.cancel")
        grok.send(b"\x20\x00" * 4)  # late audio of the cancelled response
        grok.send({"type": "response.output_audio_transcript.delta", "response_id": "r1", "delta": "stale"})
        grok.send({"type": "response.done", "response": {"id": "r1", "status": "cancelled"}})

        say(grok, "never mind")
        answer(grok, "fresh", "r2")
        seen: list[str | bytes] = []
        while True:
            message = ws.receive()
            text = message.get("text")
            if text is not None and json.loads(text)["type"] == "transcript.delta" and json.loads(text)["role"] == "assistant":
                assert json.loads(text)["text"] == "fresh"
                break
            seen.append(text if text is not None else message["bytes"])
        assert b"\x20\x00" * 4 not in seen


def test_interrupt_cancels_the_active_response(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "go on")
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send({"type": "response.output_audio_transcript.delta", "response_id": "r1", "delta": "So"})
        assert next_event(ws, "transcript.delta")["role"] == "user"
        assert next_event(ws, "transcript.delta") == {"type": "transcript.delta", "role": "assistant", "text": "So"}
        ws.send_text(json.dumps({"type": "interrupt"}))
        assert grok.next_of("response.cancel")["type"] == "response.cancel"
        assert next_event(ws, "state") == {"type": "state", "state": "listening", "detail": "barge-in"}


def test_empty_transcript_cancels_instead_of_running_a_turn(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        # Server VAD answers the committed noise before its (empty) transcript arrives.
        grok.send({"type": "input_audio_buffer.speech_stopped"})
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u", "transcript": "   "})
        assert grok.next_json()["type"] == "response.cancel"


def test_missing_key_is_reported_not_faked(grok: FakeGrok) -> None:
    with make_client(Settings(xai_base_url=grok.base_url)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "session.start"}))
            state = next_event(ws, "state")
            assert state == {"type": "state", "state": "idle", "detail": "xAI is not connected: add a key in Providers"}


def test_picker_models_are_the_registry_realtime_models(client: TestClient) -> None:
    body = client.get("/voice-models").json()
    assert [(m["id"], m["shortLabel"]) for m in body["models"]] == [("grok-voice-latest", "Latest"), ("grok-voice-3.0", "3.0")]
    assert body["selection"] == {"model": "grok-voice-latest", "voice": "eve", "effort": "high"}


def test_core_server_routes_are_mounted(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True

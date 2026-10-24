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


def call(grok: FakeGrok, name: str, args: dict[str, Any], call_id: str) -> None:
    grok.send({"type": "response.created", "response": {"id": "r1"}})
    grok.send(
        {
            "type": "response.function_call_arguments.done",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(args),
        }
    )


def test_session_start_configures_grok_server_side(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        update = start(ws, grok)
        assert grok.headers.get(timeout=5) == "Bearer test-key"
        session = update["session"]
        assert session["voice"] == "eve"
        assert session["turn_detection"] == {"type": "server_vad"}
        assert session["audio"]["input"] == {"format": {"type": "audio/pcm", "rate": 24000}, "transport": "binary"}
        names = {tool.get("name", tool["type"]) for tool in session["tools"]}
        assert {"web_search", "render_widget", "update_widget", "get_weather", "get_stock_history"} <= names

        ws.send_bytes(b"\x01\x00\x02\x00")
        assert grok.next_bytes() == b"\x01\x00\x02\x00"


def test_render_widget_validates_pushes_and_reports_shown(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        call(grok, "render_widget", WEATHER_CARD, "c1")
        render = next_event(ws, "widget.render")
        widget_id = render["widget"]["id"]
        assert re.fullmatch(r"w1-[0-9a-f]{8}", widget_id)
        assert render["widget"] == {"id": widget_id, "type": "card", "props": WEATHER_CARD["props"]}

        output = grok.next_of("conversation.item.create")["item"]
        assert output["type"] == "function_call_output" and output["call_id"] == "c1"
        assert json.loads(output["output"]) == {"shown": 'card "Palo Alto weekend"', "id": widget_id}

        grok.send({"type": "response.done"})
        assert grok.next_json()["type"] == "response.create"


def test_invalid_widget_never_reaches_canvas(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        call(grok, "render_widget", {"type": "chart", "props": {"kind": "pie", "series": []}}, "c2")
        result = json.loads(grok.next_of("conversation.item.create")["item"]["output"])
        assert result["shown"] is None and "invalid widget" in result["error"]
        grok.send({"type": "response.done"})
        grok.next_of("response.create")
        # Next event the browser sees is state, not a widget.render.
        message = json.loads(ws.receive_text())
        assert message["type"] == "state"


def test_widget_action_becomes_a_user_turn(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        call(grok, "render_widget", DINNER, "c3")
        widget_id = next_event(ws, "widget.render")["widget"]["id"]
        grok.next_of("conversation.item.create")
        grok.send({"type": "response.done"})
        grok.next_of("response.create")

        ws.send_text(json.dumps({"type": "widget.action", "id": widget_id, "action": "select", "value": "ramen"}))
        item = grok.next_of("conversation.item.create")["item"]
        assert item["role"] == "user"
        assert item["content"][0]["text"] == (
            f"[Tapped on screen] options widget {widget_id}: select -> option 2, Ramen counter"
        )
        assert grok.next_json()["type"] == "response.create"


def test_update_widget_streams_a_validated_patch(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        call(grok, "render_widget", WEATHER_CARD, "c4")
        widget_id = next_event(ws, "widget.render")["widget"]["id"]
        grok.send(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "c5",
                "name": "update_widget",
                "arguments": json.dumps({"id": widget_id, "patch": {"title": "Updated"}}),
            }
        )
        assert next_event(ws, "widget.update") == {"type": "widget.update", "id": widget_id, "patch": {"title": "Updated"}}


def test_update_touches_only_the_targeted_widget(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        call(grok, "render_widget", WEATHER_CARD, "c6")
        first = next_event(ws, "widget.render")["widget"]["id"]
        grok.next_of("conversation.item.create")
        call(grok, "render_widget", WEATHER_CARD, "c7")
        second = next_event(ws, "widget.render")["widget"]["id"]
        grok.next_of("conversation.item.create")
        assert first != second

        call(grok, "update_widget", {"id": second, "patch": {"title": "Only the new one"}}, "c8")
        assert next_event(ws, "widget.update") == {
            "type": "widget.update",
            "id": second,
            "patch": {"title": "Only the new one"},
        }
        assert json.loads(grok.next_of("conversation.item.create")["item"]["output"])["id"] == second


def test_widget_ids_never_repeat_across_sessions(client: TestClient, grok: FakeGrok) -> None:
    """The browser keeps earlier widgets on screen after End + restart, so a new session must not reuse their ids."""
    ids = []
    for call_id in ("s1", "s2"):
        with client.websocket_connect("/ws") as ws:
            start(ws, grok)
            call(grok, "render_widget", WEATHER_CARD, call_id)
            ids.append(next_event(ws, "widget.render")["widget"]["id"])
            grok.next_of("conversation.item.create")
    assert ids[0] != ids[1]


def test_update_for_an_id_from_another_session_is_rejected(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        call(grok, "update_widget", {"id": "w1", "patch": {"title": "x"}}, "c9")
        result = json.loads(grok.next_of("conversation.item.create")["item"]["output"])
        assert "no widget 'w1'" in result["error"]


def test_audio_flows_and_barge_in_mutes_until_next_response(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.send({"type": "response.created"})
        grok.send(b"\x10\x00" * 4)
        assert next_event(ws, "state")["state"] == "thinking"
        assert next_event(ws, "state")["state"] == "speaking"
        assert ws.receive_bytes() == b"\x10\x00" * 4

        grok.send({"type": "input_audio_buffer.speech_started", "item_id": "u1"})
        grok.send(b"\x20\x00" * 4)  # stale audio from the cancelled turn
        grok.send({"type": "response.output_audio_transcript.delta", "item_id": "a1", "delta": "stale"})
        grok.send({"type": "response.created"})
        grok.send({"type": "response.output_audio_transcript.delta", "item_id": "a2", "delta": "fresh"})
        assert next_event(ws, "state") == {"type": "state", "state": "listening", "detail": "barge-in"}
        seen: list[str | bytes] = []
        while True:
            message = ws.receive()
            text = message.get("text")
            if text is not None and json.loads(text)["type"] == "transcript.delta":
                assert json.loads(text)["text"] == "fresh"
                break
            seen.append(text if text is not None else message["bytes"])
        assert b"\x20\x00" * 4 not in seen


def test_interrupt_cancels_the_active_response(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.send({"type": "response.created"})
        assert next_event(ws, "state")["state"] == "thinking"
        ws.send_text(json.dumps({"type": "interrupt"}))
        assert grok.next_of("response.cancel")["type"] == "response.cancel"
        assert next_event(ws, "state") == {"type": "state", "state": "listening", "detail": "barge-in"}


def test_user_transcript_is_forwarded(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.send(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "u9",
                "transcript": "What's the weather?",
            }
        )
        assert next_event(ws, "transcript.delta") == {
            "type": "transcript.delta",
            "role": "user",
            "text": "What's the weather?",
            "itemId": "u9",
        }


def test_missing_key_is_reported_not_faked(grok: FakeGrok) -> None:
    with make_client(Settings(xai_api_key=None, realtime_url=grok.url)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "session.start"}))
            state = next_event(ws, "state")
            assert state == {"type": "state", "state": "idle", "detail": "XAI_API_KEY is not configured on the server"}


def test_core_server_routes_are_mounted(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True

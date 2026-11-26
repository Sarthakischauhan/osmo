from __future__ import annotations

import json
import logging
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from conftest import FakeGrok, make_client
from symphony_server.settings import Settings

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


def heard(ws: WebSocketTestSession) -> dict[str, Any]:
    """The user's final transcript, past the empty bubble opened when their speech stopped."""
    while True:
        event = next_event(ws, "transcript.delta")
        if event["role"] == "user" and event.get("final"):
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
        assert names == {
            "web_search", "set_voice",
            "run_agent", "agent_status", "cancel_agent",
            "vault_search", "vault_read", "vault_write",
            "calendar_events", "calendar_add_event", "automation_create",
            "music_play", "music_control", "music_status",
        }
        assert all(tool["type"] == "function" for tool in session["tools"])

        ws.send_bytes(b"\x01\x00\x02\x00")
        assert grok.next_bytes() == b"\x01\x00\x02\x00"


def test_the_first_utterance_names_the_thread(client: TestClient, grok: FakeGrok) -> None:
    """The chat shell's thread takes its title from the conversation, not the default."""
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "session.start", "voice": "eve"}))
        opened = next_event(ws, "thread")["thread"]
        assert opened["title"] == "New conversation"
        assert next_event(ws, "state")["state"] == "listening"
        say(grok, "  Weather this weekend in Palo Alto?  ")
        renamed = next_event(ws, "thread")["thread"]
        assert renamed["id"] == opened["id"]
        assert renamed["title"] == "Weather this weekend in Palo Alto"
        say(grok, "And Sunday?")
        # A later line does not replace a title taken from the conversation.
        assert heard(ws)["text"] == "And Sunday?"


def test_a_spoken_turn_runs_a_voice_tool(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "What's playing?")
        assert next_event(ws, "transcript.delta") == {"type": "transcript.delta", "role": "user", "text": "", "itemId": "u1"}
        assert heard(ws) == {"type": "transcript.delta", "role": "user", "text": "What's playing?", "itemId": "u1", "final": True}

        call(grok, "music_status", {}, "c1")
        assert next_event(ws, "state")["state"] == "working"
        assert tool_output(grok)["playing"] is False

        answer(grok, "Nothing right now.", "r2")
        assert next_event(ws, "transcript.delta").items() >= {"role": "assistant", "text": "Nothing right now."}.items()
        wait_listening(ws)


def test_grok_string_status_details_does_not_discard_tool_calls(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "what's playing")
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send({"type": "response.function_call_arguments.done", "response_id": "r1", "call_id": "c1", "name": "music_status", "arguments": "{}"})
        grok.send({"type": "response.done", "response": {"id": "r1", "status": "completed", "status_details": "unimplemented"}})
        assert tool_output(grok)["playing"] is False


def test_the_voice_model_cannot_draw_widgets(client: TestClient, grok: FakeGrok) -> None:
    """Widgets are the background agent's: the voice model's render_widget call is an unknown tool."""
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "show me")
        call(grok, "render_widget", DINNER, "c1")
        assert tool_output(grok) == {"error": "unknown tool render_widget"}


def test_realtime_reasoning_deltas_are_streamed_to_the_browser(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "think about this")
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send({"type": "response.reasoning_summary_text.delta", "response_id": "r1", "delta": "Considering options..."})
        grok.send({"type": "response.output_audio_transcript.delta", "response_id": "r1", "delta": "Here is my answer."})
        grok.send({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
        reasoning_event = next_event(ws, "transcript.delta")
        while reasoning_event.get("reasoning") != "Considering options...":
            reasoning_event = next_event(ws, "transcript.delta")
        assert reasoning_event["role"] == "assistant"


def test_a_model_switch_replays_the_thread(client: TestClient, grok: FakeGrok) -> None:
    """History lives in the stored thread: a new voice session on another model is seeded from it."""
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "show me")
        answer(grok, "Done.", "h1")
        wait_listening(ws)
        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "eve"}))
        assert next_event(ws, "voice.configured")["selection"]["model"] == "grok-voice-3.0"
        assert grok.closed.get(timeout=5) == "/v1/realtime?model=grok-voice-latest"
        grok.next_of("session.update")

        replayed = [grok.next_of("conversation.item.create")["item"] for _ in range(2)]
        assert [replayed_entry(item) for item in replayed] == [("user", "show me"), ("assistant", "Done.")]


def replayed_entry(item: dict[str, Any]) -> tuple[str, str]:
    """A replayed item as (role or tool item type, text / tool name / call id)."""
    if item["type"] == "function_call":
        return "function_call", item["name"]
    if item["type"] == "function_call_output":
        return "function_call_output", item["call_id"]
    return item["role"], item["content"][0]["text"]


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


def test_barge_in_recovers_when_cancelled_response_never_finishes(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "first question")
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send({"type": "response.output_audio_transcript.delta", "response_id": "r1", "delta": "First"})
        assert heard(ws)["text"] == "first question"
        assert next_event(ws, "transcript.delta")["text"] == "First"
        grok.send({"type": "input_audio_buffer.speech_started"})
        assert next_event(ws, "state")["detail"] == "barge-in"
        grok.next_of("response.cancel")
        # Some providers never send response.done for the cancelled response.
        say(grok, "next question")
        answer(grok, "Next answer", "r2")
        assert heard(ws)["text"] == "next question"
        assert next_event(ws, "transcript.delta")["text"] == "Next answer"


def test_interrupt_cancels_the_active_response(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "go on")
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send({"type": "response.output_audio_transcript.delta", "response_id": "r1", "delta": "So"})
        assert heard(ws)["text"] == "go on"
        assert next_event(ws, "transcript.delta").items() >= {"role": "assistant", "text": "So"}.items()
        ws.send_text(json.dumps({"type": "interrupt"}))
        assert grok.next_of("response.cancel")["type"] == "response.cancel"
        assert next_event(ws, "state") == {"type": "state", "state": "listening", "detail": "barge-in"}


def test_a_transcript_landing_mid_answer_stays_above_it(client: TestClient, grok: FakeGrok) -> None:
    """Transcription finishes after the answer has started: the utterance's bubble opened when
    speech stopped holds its place, and the thread stores the utterance before the answer."""
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.send({"type": "input_audio_buffer.speech_stopped"})
        assert next_event(ws, "transcript.delta") == {"type": "transcript.delta", "role": "user", "text": "", "itemId": "u1"}
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send({"type": "response.output_audio_transcript.delta", "response_id": "r1", "delta": "Just a chat."})
        assert next_event(ws, "transcript.delta")["role"] == "assistant"
        grok.send({"type": "response.done", "response": {"id": "r1"}})
        assert next_event(ws, "state")["state"] == "listening"
        grok.send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u", "transcript": "What do you mean?"})
        assert heard(ws) == {"type": "transcript.delta", "role": "user", "text": "What do you mean?", "itemId": "u1", "final": True}

        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "eve"}))
        assert next_event(ws, "voice.configured")["selection"]["model"] == "grok-voice-3.0"
        replayed = [grok.next_of("conversation.item.create")["item"] for _ in range(2)]
        assert [replayed_entry(item) for item in replayed] == [("user", "What do you mean?"), ("assistant", "Just a chat.")]


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


def test_picker_models_are_the_catalog_realtime_models(client: TestClient) -> None:
    body = client.get("/voice-models").json()
    assert [(m["id"], m["shortLabel"]) for m in body["models"] if m["provider"] == "xai"] == [
        ("grok-voice-latest", "Latest"),
        ("grok-voice-3.0", "3.0"),
        ("grok-voice-2.0", "2.0"),
    ]
    assert body["selection"] == {"model": "grok-voice-latest", "voice": "eve", "effort": "high"}


def test_core_server_routes_are_mounted(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_desktop_token_is_echoed_only_to_its_holder(grok: FakeGrok) -> None:
    with make_client(Settings(xai_base_url=grok.base_url, app_token="desktop-secret")) as client:
        anonymous = client.get("/api/health", headers={"X-Osmo-Desktop-Token": "wrong"})
        trusted = client.get("/", headers={"X-Osmo-Desktop-Token": "desktop-secret"})
    assert "x-osmo-desktop-token" not in anonymous.headers
    assert trusted.headers["x-osmo-desktop-token"] == "desktop-secret"
    assert trusted.headers["cache-control"] == "no-store"


def test_a_malformed_client_event_is_logged_without_what_was_typed(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    with client.websocket_connect("/ws") as ws:
        # A browser.key without its `code`: pydantic's error would carry the whole event, the typed key included.
        ws.send_text(json.dumps({"type": "browser.key", "id": "w1", "key": "hunter2-secret", "keyCode": 65}))
        ws.send_text(json.dumps({"type": "browser.key", "id": "w1", "key": "hunter2-secret", "code": "KeyA", "keyCode": -1}))
    deadline = time.monotonic() + 2
    while caplog.text.count("bad client event") < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert caplog.text.count("bad client event") == 2
    assert "hunter2-secret" not in caplog.text and "'input'" not in caplog.text

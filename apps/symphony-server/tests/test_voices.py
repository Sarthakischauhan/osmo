from __future__ import annotations

import base64
import json
import queue

import pytest

from fastapi.testclient import TestClient

from conftest import FakeGrok
from symphony_server.voices import (
    PREVIEW_BYTES,
    Capabilities,
    Selection,
    Voice,
    VoiceModel,
    realtime_url_for,
    resolve_selection,
)
from test_session import call, next_event, start

MODELS = (
    VoiceModel(
        id="grok-voice-latest",
        provider="xai",
        label="Grok Voice",
        capabilities=Capabilities(tools=True, interrupt=True, customVoices=False),
        voices=[Voice(id="eve", label="Eve", traits=["bright"]), Voice(id="sal", label="Sal", traits=["deep"])],
        defaultVoice="eve",
    ),
    VoiceModel(
        id="thinker",
        provider="acme",
        label="Thinker",
        capabilities=Capabilities(tools=True, interrupt=False, customVoices=False, reasoningEffort=["low", "high"]),
        voices=[Voice(id="nova", label="Nova", traits=["clear"]), Voice(id="rex", label="Rex", traits=["low"])],
        defaultVoice="rex",
    ),
)
CURRENT = Selection(model="grok-voice-latest", voice="eve")


def test_voice_only_change_keeps_model() -> None:
    assert resolve_selection(MODELS, {"voice": "Sal"}, CURRENT) == Selection(model="grok-voice-latest", voice="sal")


def test_model_change_takes_default_voice_and_an_allowed_effort() -> None:
    assert resolve_selection(MODELS, {"model": "thinker"}, CURRENT) == Selection(model="thinker", voice="rex", effort="low")
    picked = resolve_selection(MODELS, {"model": "thinker", "voice": "nova", "effort": "high"}, CURRENT)
    assert picked == Selection(model="thinker", voice="nova", effort="high")


def test_rejects_unknown_ids_unsupported_effort_and_extra_keys() -> None:
    assert "unknown model" in str(resolve_selection(MODELS, {"model": "nope"}, CURRENT))
    assert "unknown voice" in str(resolve_selection(MODELS, {"voice": "nova"}, CURRENT))
    assert "does not support" in str(resolve_selection(MODELS, {"effort": "high"}, CURRENT))
    assert "does not support" in str(resolve_selection(MODELS, {"model": "thinker", "effort": "medium"}, CURRENT))
    assert "invalid arguments" in str(resolve_selection(MODELS, {"speed": 2}, CURRENT))
    assert "invalid arguments" in str(resolve_selection(MODELS, {"effort": "max"}, CURRENT))


def test_realtime_url_model_param_is_replaced() -> None:
    assert realtime_url_for("wss://x/v1/realtime?model=a&k=1", "b") == "wss://x/v1/realtime?k=1&model=b"


def test_models_event_on_connect(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        event = next_event(ws, "models")
        assert event["selection"] == {"model": "grok-voice-latest", "voice": "eve", "effort": "high"}
        assert [v["id"] for v in event["models"][0]["voices"]] == ["eve", "ara", "sal"]


def test_set_voice_tool_reconfigures_and_notifies_browser(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        call(grok, "set_voice", {"voice": "sal"}, "v1")
        assert grok.next_of("session.update")["session"]["voice"] == "sal"
        result = json.loads(grok.next_of("conversation.item.create")["item"]["output"])
        assert result["configured"] == {"model": "grok-voice-latest", "voice": "sal", "effort": "high"}
        event = next_event(ws, "voice.configured")
        assert event == {"type": "voice.configured", "selection": {"model": "grok-voice-latest", "voice": "sal", "effort": "high"}, "source": "tool"}


def test_set_voice_tool_rejects_invalid_voice(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        call(grok, "set_voice", {"voice": "darth"}, "v2")
        result = json.loads(grok.next_of("conversation.item.create")["item"]["output"])
        assert "unknown voice" in result["error"]


def test_session_configure_from_picker(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-latest", "voice": "ara"}))
        assert grok.next_of("session.update")["session"]["voice"] == "ara"
        assert next_event(ws, "voice.configured")["source"] == "client"


def test_voice_preview_generated_once_then_cached(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "voice.preview", "requestId": "p1", "model": "grok-voice-latest", "voice": "sal"}))
        assert grok.next_of("session.update")["session"]["voice"] == "sal"
        grok.next_of("response.create")
        grok.send(b"\x01\x00" * (PREVIEW_BYTES // 2 + 500))
        first = next_event(ws, "voice.preview")
        assert first["requestId"] == "p1" and len(base64.b64decode(first["audio"])) == PREVIEW_BYTES
        ws.send_text(json.dumps({"type": "voice.preview", "requestId": "p2", "model": "grok-voice-latest", "voice": "sal"}))
        second = next_event(ws, "voice.preview")
        assert second["requestId"] == "p2" and second["audio"] == first["audio"]


def test_voice_and_effort_change_is_a_session_update_on_the_live_upstream(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        assert grok.paths.get(timeout=5).endswith("model=grok-voice-latest")
        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-latest", "voice": "sal", "effort": "none"}))
        session = grok.next_of("session.update")["session"]
        assert session["voice"] == "sal" and session["reasoning"] == {"effort": "none"}
        assert next_event(ws, "voice.configured")["selection"]["effort"] == "none"
        # Same upstream: nothing new opened, nothing closed.
        with pytest.raises(queue.Empty):
            grok.paths.get(timeout=0.3)
        assert grok.closed.empty()


def test_model_change_hot_swaps_upstream_and_carries_the_conversation(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        assert grok.paths.get(timeout=5).endswith("model=grok-voice-latest")
        grok.send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "My name is Ada."})
        assert next_event(ws, "transcript.delta")["role"] == "user"
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        for delta in ("Nice to meet ", "you, Ada."):
            grok.send({"type": "response.output_audio_transcript.delta", "item_id": "a1", "delta": delta})
        grok.send({"type": "response.done"})
        assert next_event(ws, "state")["state"] == "thinking"
        assert next_event(ws, "state")["state"] == "listening"

        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "ara"}))
        assert grok.paths.get(timeout=5).endswith("model=grok-voice-3.0")
        assert grok.next_of("session.update")["session"]["voice"] == "ara"
        items = [grok.next_of("conversation.item.create")["item"] for _ in range(2)]
        assert items == [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "My name is Ada."}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Nice to meet you, Ada."}]},
        ]
        assert grok.closed.get(timeout=5).endswith("model=grok-voice-latest")
        configured = next_event(ws, "voice.configured")
        assert configured["selection"] == {"model": "grok-voice-3.0", "voice": "ara", "effort": "high"}

        # The browser socket never dropped: audio keeps flowing to the new upstream.
        ws.send_bytes(b"\x02\x00" * 8)
        assert grok.next_bytes() == b"\x02\x00" * 8


def test_set_voice_model_switch_hot_swaps_and_lets_the_new_model_confirm(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.paths.get(timeout=5)
        grok.send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Use Grok Voice 3."})
        call(grok, "set_voice", {"model": "grok-voice-3.0"}, "v9")
        assert grok.paths.get(timeout=5).endswith("model=grok-voice-3.0")
        grok.next_of("session.update")
        assert grok.next_of("conversation.item.create")["item"]["content"][0]["text"] == "Use Grok Voice 3."
        grok.next_of("response.create")
        assert next_event(ws, "voice.configured")["source"] == "tool"


def test_voice_models_endpoint_serves_the_registry_before_any_socket(client: TestClient) -> None:
    body = client.get("/voice-models").json()
    assert body["type"] == "models"
    assert [m["id"] for m in body["models"]] == ["grok-voice-latest", "grok-voice-3.0"]
    assert body["models"][0]["capabilities"]["reasoningEffort"] == ["high", "none"]
    assert body["selection"] == {"model": "grok-voice-latest", "voice": "eve", "effort": "high"}

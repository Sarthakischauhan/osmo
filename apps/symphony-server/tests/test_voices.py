from __future__ import annotations

import base64
import json

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
        assert event["selection"] == {"model": "grok-voice-latest", "voice": "eve"}
        assert [v["id"] for v in event["models"][0]["voices"]] == ["eve", "ara", "sal"]


def test_set_voice_tool_reconfigures_and_notifies_browser(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        call(grok, "set_voice", {"voice": "sal"}, "v1")
        assert grok.next_of("session.update")["session"]["voice"] == "sal"
        result = json.loads(grok.next_of("conversation.item.create")["item"]["output"])
        assert result["configured"] == {"model": "grok-voice-latest", "voice": "sal"}
        event = next_event(ws, "voice.configured")
        assert event == {"type": "voice.configured", "selection": {"model": "grok-voice-latest", "voice": "sal"}, "source": "tool"}


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

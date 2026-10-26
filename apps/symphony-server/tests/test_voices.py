from __future__ import annotations

import base64
import json
import queue
from typing import Any

import pytest

from fastapi.testclient import TestClient


from conftest import FakeGrok
from symphony_server.voices import (
    PREVIEW_BYTES,
    Capabilities,
    Selection,
    Voice,
    VoiceModel,
    SetVoiceArgs,
    parse_voice_change,
    resolve_selection,
    set_voice_description,
    voice_models,
)
from test_session import answer, call, next_event, say, start, tool_output, wait_listening

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
    assert resolve_selection(MODELS, SetVoiceArgs(voice="Sal"), CURRENT) == Selection(model="grok-voice-latest", voice="sal")


def test_model_change_takes_default_voice_and_an_allowed_effort() -> None:
    # Without "medium" on offer the default is the model's last (highest) listed effort.
    assert resolve_selection(MODELS, SetVoiceArgs(model="thinker"), CURRENT) == Selection(model="thinker", voice="rex", effort="high")
    picked = resolve_selection(MODELS, SetVoiceArgs(model="thinker", voice="nova", effort="high"), CURRENT)
    assert picked == Selection(model="thinker", voice="nova", effort="high")


def test_rejects_unknown_ids_unsupported_effort_and_extra_keys() -> None:
    assert "unknown model" in str(resolve_selection(MODELS, SetVoiceArgs(model="nope"), CURRENT))
    assert "unknown voice" in str(resolve_selection(MODELS, SetVoiceArgs(voice="nova"), CURRENT))
    assert "does not support" in str(resolve_selection(MODELS, SetVoiceArgs(effort="high"), CURRENT))
    assert "does not support" in str(resolve_selection(MODELS, SetVoiceArgs(model="thinker", effort="medium"), CURRENT))
    assert "invalid arguments" in str(parse_voice_change({"speed": 2}))
    assert "invalid arguments" in str(parse_voice_change({"effort": "max"}))


def test_models_event_on_connect(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        event = next_event(ws, "models")
        assert event["selection"] == {"model": "grok-voice-latest", "voice": "eve", "effort": "high"}
        assert [v["id"] for v in event["models"][0]["voices"]] == [v.id for v in voice_models()[0].voices]


def frames_until(grok: FakeGrok, *types: str) -> list[dict[str, Any]]:
    """Every JSON frame Symphony sends until one of each of `types` has gone by (the bridge's
    session.update and the harness's tool result travel independently)."""
    frames: list[dict[str, Any]] = []
    while not set(types) <= {f["type"] for f in frames}:
        frames.append(grok.next_json())
    return frames


def test_set_voice_tool_reconfigures_and_notifies_browser(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "Use Sal.")
        call(grok, "set_voice", {"voice": "sal"}, "v1")
        event = next_event(ws, "voice.configured")
        assert event == {"type": "voice.configured", "selection": {"model": "grok-voice-latest", "voice": "sal", "effort": "high"}, "source": "tool"}
        frames = frames_until(grok, "session.update", "response.create")
        assert [f["session"]["voice"] for f in frames if f["type"] == "session.update"] == ["sal"]
        output = next(f["item"] for f in frames if f["type"] == "conversation.item.create")
        # The tool reports the change it handed over; the bridge resolves it against the live selection.
        assert json.loads(output["output"])["configured"] == {"voice": "sal"}


def test_set_voice_tool_rejects_invalid_voice(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "Use Darth.")
        call(grok, "set_voice", {"voice": "darth"}, "v2")
        assert "unknown voice" in tool_output(grok)["error"]


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


def test_model_change_opens_a_new_voice_session_seeded_from_the_harness(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        assert grok.paths.get(timeout=5).endswith("model=grok-voice-latest")
        say(grok, "My name is Ada.")
        answer(grok, "Nice to meet you, Ada.", "r1")
        wait_listening(ws)

        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "ara"}))
        assert grok.paths.get(timeout=5).endswith("model=grok-voice-3.0")
        assert grok.next_of("session.update")["session"]["voice"] == "ara"
        assert grok.closed.get(timeout=5).endswith("model=grok-voice-latest")
        configured = next_event(ws, "voice.configured")
        assert configured["selection"] == {"model": "grok-voice-3.0", "voice": "ara", "effort": "high"}

        # The browser socket never dropped: audio keeps flowing to the new session.
        ws.send_bytes(b"\x02\x00" * 8)
        assert grok.next_bytes() == b"\x02\x00" * 8

        # Its first turn replays the harness history before the speech it just heard.
        say(grok, "What's my name?")
        items = [grok.next_of("conversation.item.create")["item"] for _ in range(2)]
        assert items == [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "My name is Ada."}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Nice to meet you, Ada."}]},
        ]


def test_set_voice_model_switch_happens_after_the_confirming_turn(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.paths.get(timeout=5)
        say(grok, "Use Grok Voice 3.")
        call(grok, "set_voice", {"model": "grok-voice-3.0"}, "v9")
        assert tool_output(grok)["configured"]["model"] == "grok-voice-3.0"
        with pytest.raises(queue.Empty):
            grok.paths.get(timeout=0.3)  # the current session still speaks the confirmation
        answer(grok, "Switching now.", "r2")
        assert grok.paths.get(timeout=5).endswith("model=grok-voice-3.0")
        grok.next_of("session.update")
        assert next_event(ws, "voice.configured")["source"] == "tool"


def test_voice_models_endpoint_serves_the_catalog_before_any_socket(client: TestClient) -> None:
    body = client.get("/voice-models").json()
    assert body["type"] == "models"
    assert [m["id"] for m in body["models"]] == ["grok-voice-latest", "grok-voice-3.0", "grok-voice-2.0"]
    assert body["models"][0]["capabilities"]["reasoningEffort"] == ["none", "high"]
    assert body["selection"] == {"model": "grok-voice-latest", "voice": "eve", "effort": "high"}


def test_model_ring_short_labels_are_distinct_and_served(client: TestClient) -> None:
    shorts = [m.shortLabel for m in voice_models()]
    assert shorts == ["Latest", "3.0", "2.0"]
    assert len(set(shorts)) == len(shorts)
    served = client.get("/voice-models").json()["models"]
    assert [(m["id"], m["shortLabel"]) for m in served] == [
        ("grok-voice-latest", "Latest"),
        ("grok-voice-3.0", "3.0"),
        ("grok-voice-2.0", "2.0"),
    ]


def test_set_voice_description_lists_traits_only_where_the_catalog_has_them() -> None:
    model = voice_models()[0]
    plain = model.model_copy(update={"voices": [Voice(id="eve", label="Eve", traits=[])]})
    described = model.model_copy(update={"voices": [Voice(id="sal", label="Sal", traits=["deep"])]})
    text = set_voice_description((plain, described))
    assert f"- {model.id}: voices eve;" in text
    assert f"- {model.id}: voices sal (deep);" in text

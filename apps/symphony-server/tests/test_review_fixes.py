"""PR #8 review fixes: preview decoding, validation, rate limits and LRU bounds; configure
confirmations; waiting picker swaps; mic pause during previews; replay cap."""

from __future__ import annotations

import asyncio
import base64
import json
import queue
import time

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from conftest import VOICE_LIMITS, FakeGrok, xai_key_store
from core_ai import ModelRegistry
from core_ai.voice import VoiceRegistry

from symphony_server import session_previews
from symphony_server.model_access import ModelAccess
from symphony_server.providers import CredentialStore
from symphony_server.voice_bridge import VoiceBridge
from symphony_server.voices import (
    PREVIEW_BYTES,
    PREVIEW_RATE_LIMIT,
    KeyedRateLimiter,
    PreviewCache,
    RateLimiter,
)
from test_session import next_event, say, start

FAKE_AUDIO = b"\x05\x00" * (PREVIEW_BYTES // 2)


def ask_preview(ws: WebSocketTestSession, request_id: str, model: str = "grok-voice-latest", voice: str = "sal") -> None:
    ws.send_text(json.dumps({"type": "voice.preview", "requestId": request_id, "model": model, "voice": voice}))


@pytest.fixture
def canned_previews(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Previews generated without an upstream; records each generation."""
    made: list[tuple[str, str]] = []

    async def generate(self: PreviewCache, model: str, voice: str) -> bytes:
        made.append((model, voice))
        return FAKE_AUDIO

    monkeypatch.setattr(PreviewCache, "generate", generate)
    return made


# ---------------------------------------------------------------- 1. JSON audio deltas


def test_preview_decodes_base64_json_audio_deltas(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        ask_preview(ws, "p1")
        grok.next_of("response.create")
        half = PREVIEW_BYTES // 2
        for chunk in (b"\x01\x00" * (half // 2), b"\x02\x00" * (half // 2)):
            grok.send({"type": "response.output_audio.delta", "delta": base64.b64encode(chunk).decode()})
        grok.send({"type": "response.done"})
        audio = base64.b64decode(next_event(ws, "voice.preview")["audio"])
        assert audio == b"\x01\x00" * (half // 2) + b"\x02\x00" * (half // 2)


# ---------------------------------------------------------------- 2. validation, rate limit, LRU


def test_preview_rejects_unknown_model_or_voice_before_any_upstream(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        ask_preview(ws, "bad-model", model="nope")
        assert "unknown model" in next_event(ws, "voice.preview")["error"]
        ask_preview(ws, "bad-voice", voice="nope")
        assert "unknown voice" in next_event(ws, "voice.preview")["error"]
    with pytest.raises(queue.Empty):
        grok.paths.get(timeout=0.3)


def test_preview_is_rate_limited_per_socket(client: TestClient, canned_previews: list[tuple[str, str]]) -> None:
    with client.websocket_connect("/ws") as ws:
        for n in range(PREVIEW_RATE_LIMIT):
            ask_preview(ws, f"p{n}")
            assert next_event(ws, "voice.preview")["audio"]
        ask_preview(ws, "over")
        assert "too many previews" in next_event(ws, "voice.preview")["error"]
    with client.websocket_connect("/ws") as other:
        ask_preview(other, "fresh-socket")
        assert next_event(other, "voice.preview")["audio"]


def test_preview_cache_is_lru_bounded_and_drops_idle_locks(canned_previews: list[tuple[str, str]]) -> None:
    access = ModelAccess(ModelRegistry(), VoiceRegistry(VOICE_LIMITS), CredentialStore(xai_key_store("key")), "http://unused/v1")
    cache = PreviewCache(access, None, max_entries=2)

    async def run() -> None:
        await cache.get("grok-voice-latest", "eve")
        await cache.get("grok-voice-latest", "ara")
        await cache.get("grok-voice-latest", "eve")  # eve is now most recent
        await cache.get("grok-voice-latest", "sal")  # evicts ara
        assert list(cache.memory) == [("grok-voice-latest", "eve"), ("grok-voice-latest", "sal")]
        assert cache.locks == {} and cache.waiters == {}
        with pytest.raises(ValueError):
            await cache.get("grok-voice-latest", "nope")

    asyncio.run(run())
    assert canned_previews.count(("grok-voice-latest", "eve")) == 1


def test_rate_limiters_slide_and_bound_their_keys() -> None:
    now = [0.0]
    limiter = RateLimiter(2, 10.0, clock=lambda: now[0])
    assert limiter.allow() and limiter.allow() and not limiter.allow()
    now[0] = 10.0
    assert limiter.allow()
    keyed = KeyedRateLimiter(1, 10.0, max_keys=2, clock=lambda: now[0])
    assert keyed.allow("a") and keyed.allow("b") and keyed.allow("c")
    assert list(keyed.limiters) == ["b", "c"]


# ---------------------------------------------------------------- 3. configure always confirms


def test_rejected_configure_reports_the_selection_still_in_effect(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-latest", "voice": "nope"}))
        configured = next_event(ws, "voice.configured")
        assert "unknown voice" in configured["error"]
        assert configured["selection"] == {"model": "grok-voice-latest", "voice": "eve", "effort": "high"}


def test_failed_hot_swap_reports_the_previous_selection(
    client: TestClient, grok: FakeGrok, monkeypatch: pytest.MonkeyPatch
) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)

        async def unreachable(self: VoiceBridge) -> bool:
            return False

        monkeypatch.setattr(VoiceBridge, "open_voice", unreachable)
        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "ara"}))
        configured = next_event(ws, "voice.configured")
        assert configured["error"].startswith("could not open grok-voice-3.0")
        assert configured["selection"]["model"] == "grok-voice-latest" and configured["selection"]["voice"] == "eve"


def test_session_start_reports_the_applied_pick(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "session.start", "model": "grok-voice-3.0", "voice": "sal", "effort": "none"}))
        configured = next_event(ws, "voice.configured")
        assert configured["selection"] == {"model": "grok-voice-3.0", "voice": "sal", "effort": "none"}
        assert "error" not in configured


# ---------------------------------------------------------------- 4. picker swap waits for the turn


def test_picker_model_switch_waits_for_the_in_flight_response_then_listens(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        assert grok.paths.get(timeout=5).endswith("model=grok-voice-latest")
        say(grok, "Tell me a joke.")
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        assert next_event(ws, "transcript.delta")["role"] == "user"
        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "ara"}))
        with pytest.raises(queue.Empty):
            grok.paths.get(timeout=0.4)  # no swap while the turn is running
        grok.send({"type": "response.done", "response": {"id": "r1"}})
        assert next_event(ws, "state")["state"] == "listening"
        assert grok.paths.get(timeout=5).endswith("model=grok-voice-3.0")
        assert next_event(ws, "voice.configured")["selection"]["model"] == "grok-voice-3.0"


def test_picker_swap_after_speaking_returns_to_listening(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.paths.get(timeout=5)
        grok.send(b"\x01\x00" * 4)  # assistant audio: state speaking, no response in flight
        assert next_event(ws, "state")["state"] == "speaking"
        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "ara"}))
        next_event(ws, "voice.configured")
        assert next_event(ws, "state")["state"] == "listening"


# ---------------------------------------------------------------- 6. previews only when quiet; mic paused


def test_preview_refused_while_the_assistant_speaks(
    client: TestClient, grok: FakeGrok, canned_previews: list[tuple[str, str]]
) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.send(b"\x01\x00" * 4)
        assert next_event(ws, "state")["state"] == "speaking"
        ask_preview(ws, "p1")
        assert "only while idle or listening" in next_event(ws, "voice.preview")["error"]
        assert canned_previews == []


def test_mic_is_not_forwarded_while_a_preview_plays(
    client: TestClient, grok: FakeGrok, canned_previews: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_previews, "PREVIEW_SECONDS", 0.5)
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        ask_preview(ws, "p1")
        assert next_event(ws, "voice.preview")["audio"]
        ws.send_bytes(b"\x07\x00" * 4)  # the preview, picked up by the mic
        with pytest.raises(queue.Empty):
            grok.next_bytes(timeout=0.2)
        time.sleep(0.4)
        ws.send_bytes(b"\x08\x00" * 4)
        assert grok.next_bytes() == b"\x08\x00" * 4


# ---------------------------------------------------------------- 7. GET /voice-preview


def test_voice_preview_endpoint_validates_rate_limits_and_serves_pcm(
    client: TestClient, canned_previews: list[tuple[str, str]]
) -> None:
    assert client.get("/voice-preview", params={"model": "nope", "voice": "eve"}).status_code == 400
    assert client.get("/voice-preview", params={"model": "grok-voice-latest", "voice": "nope"}).status_code == 400
    for _ in range(PREVIEW_RATE_LIMIT):
        ok = client.get("/voice-preview", params={"model": "grok-voice-latest", "voice": "ara"})
        assert ok.status_code == 200 and ok.content == FAKE_AUDIO
        assert ok.headers["content-type"].startswith("audio/L16")
    assert client.get("/voice-preview", params={"model": "grok-voice-latest", "voice": "ara"}).status_code == 429
    assert canned_previews == [("grok-voice-latest", "ara")]

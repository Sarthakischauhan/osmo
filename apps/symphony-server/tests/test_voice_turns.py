"""The voice bridge owns turns: history compacts to the model it switches to, and only requested
voice responses reach a turn."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import FakeGrok, make_client, xai_key_store
from core_harness.context.compact import COMPACTED_CONTEXT_MARK
from symphony_server.settings import Settings
from test_session import DINNER, answer, next_event, render, say, start, wait_listening

SMALL_LIMIT = 300
# The real grok-voice-3.0, configured with a small window so switching to it must compact.
SMALL_MODEL = "grok-voice-3.0"


@pytest.fixture
def small_client(grok: FakeGrok) -> Iterator[TestClient]:
    limits = {f"grok:{SMALL_MODEL}": SMALL_LIMIT}
    settings = Settings(xai_base_url=grok.base_url)
    with make_client(settings, secret_store=xai_key_store(), voice_context_limits=limits) as test_client:
        yield test_client


def replayed_until_response(grok: FakeGrok) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while (frame := grok.next_json())["type"] != "response.create":
        if frame["type"] == "conversation.item.create":
            items.append(frame["item"])
    return items


def test_switching_to_a_smaller_model_compacts_history_to_its_budget(small_client: TestClient, grok: FakeGrok) -> None:
    turns = [("hi", "hello")] + [(f"question {n} " + "q" * 800, f"answer {n} " + "a" * 800) for n in range(1, 4)]
    with small_client.websocket_connect("/ws") as ws:
        start(ws, grok)
        for n, (question, reply) in enumerate(turns):
            say(grok, question)
            answer(grok, reply, f"r{n}")
            wait_listening(ws)
        # The latest tool result stays whole (KeepSystemRecentCompactor protects it); the long
        # turns before it are what the smaller window drops.
        widget_id = render(ws, grok, DINNER, "h1")
        ws.send_text(json.dumps({"type": "session.configure", "model": SMALL_MODEL, "voice": "eve"}))
        assert next_event(ws, "voice.configured")["selection"]["model"] == SMALL_MODEL
        grok.next_of("session.update")

        ws.send_text(json.dumps({"type": "widget.action", "id": widget_id, "action": "select", "value": "ramen"}))
        texts = [item["content"][0]["text"] for item in replayed_until_response(grok) if item["type"] == "message"]

    assert any(text.startswith(COMPACTED_CONTEXT_MARK) for text in texts)
    assert texts[0] == "hi"
    assert not any(text.startswith(("question", "answer")) for text in texts)
    assert texts[-1].startswith("[Tapped on screen]")
    assert sum(len(text) for text in texts) // 4 <= SMALL_LIMIT


def test_an_unrequested_voice_response_never_reaches_a_turn(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        answer(grok, "spontaneous", "r9")
        say(grok, "hello")
        answer(grok, "requested", "r1")
        spoken: list[str] = []
        while (event := json.loads(ws.receive_text())) != {"type": "state", "state": "listening"} or not spoken:
            if event["type"] == "transcript.delta" and event["role"] == "assistant":
                spoken.append(event["text"])
    assert "spontaneous" not in "".join(spoken)
    assert "requested" in "".join(spoken)

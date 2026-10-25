"""Tools are real function tools on every session.update, and tool calls written as text never surface."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import FakeGrok
from symphony_server.session import TranscriptGuard
from symphony_server.settings import DEFAULT_INSTRUCTIONS
from test_session import next_event, start

FUNCTIONS = {"render_widget", "update_widget", "set_voice", "get_weather", "get_stock_history"}


def assert_native_tools(session: dict[str, Any]) -> None:
    tools = session["tools"]
    assert {"type": "web_search"} in tools
    functions = {tool["name"]: tool for tool in tools if tool["type"] == "function"}
    assert set(functions) == FUNCTIONS
    for tool in functions.values():
        assert tool["parameters"]["type"] == "object" and tool["description"]


def test_tools_are_native_on_start_after_voice_change_and_after_hot_swap(client: TestClient, grok: FakeGrok) -> None:
    with client.websocket_connect("/ws") as ws:
        assert_native_tools(start(ws, grok)["session"])

        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-latest", "voice": "sal", "effort": "none"}))
        assert_native_tools(grok.next_of("session.update")["session"])
        next_event(ws, "voice.configured")

        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "ara"}))
        swapped = grok.next_of("session.update")["session"]
        assert swapped["voice"] == "ara"
        assert_native_tools(swapped)
        assert next_event(ws, "voice.configured")["selection"]["model"] == "grok-voice-3.0"


def test_prompt_teaches_no_textual_tool_syntax() -> None:
    assert "<tool_call>" not in DEFAULT_INSTRUCTIONS
    assert "web_search" not in DEFAULT_INSTRUCTIONS


def test_function_call_output_item_runs_the_tool_once(client: TestClient, grok: FakeGrok) -> None:
    card = {"type": "card", "props": {"title": "Steps", "body": "1. install"}}
    item = {"type": "function_call", "call_id": "c9", "name": "render_widget", "arguments": json.dumps(card)}
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send({"type": "response.function_call_arguments.done", **{k: item[k] for k in ("call_id", "name", "arguments")}})
        grok.send({"type": "response.output_item.done", "item": item})
        grok.send({"type": "response.done"})
        assert next_event(ws, "widget.render")["widget"]["props"]["title"] == "Steps"
        output = grok.next_of("conversation.item.create")["item"]
        assert output["type"] == "function_call_output" and output["call_id"] == "c9"
        grok.next_of("response.create")

        # A call reported only by response.output_item.done is dispatched too.
        grok.send({"type": "response.created", "response": {"id": "r2"}})
        grok.send({"type": "response.output_item.done", "item": {**item, "call_id": "c10"}})
        assert grok.next_of("conversation.item.create")["item"]["call_id"] == "c10"


def test_tool_call_text_is_stripped_from_the_transcript_and_logged(
    client: TestClient, grok: FakeGrok, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="symphony.session")
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        for delta in ("Quick search for the current steps. <tool", "_call>web_search_ query_=shadcn", " sidebar num_results_=5"):
            grok.send({"type": "response.output_audio_transcript.delta", "item_id": "a1", "delta": delta})
        grok.send({"type": "response.done"})
        shown = next_event(ws, "transcript.delta")["text"]
        assert next_event(ws, "state")["state"] == "listening"
        assert shown == "Quick search for the current steps. "
        assert any("tool call as text" in r.getMessage() for r in caplog.records)
        assert all("shadcn" not in r.getMessage() for r in caplog.records)

        # Nor is it replayed into a hot-swapped model.
        ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "ara"}))
        replayed = grok.next_of("conversation.item.create")["item"]
        assert replayed["content"][0]["text"] == "Quick search for the current steps. "


def test_guard_releases_a_held_prefix_that_is_not_a_tag() -> None:
    guard = TranscriptGuard()
    assert guard.feed("a <to") == "a "
    assert guard.feed("p> b") == "<top> b"
    assert not guard.leaked

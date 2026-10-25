"""Tools are registered natively on every session.update and function calls dispatch exactly once."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import FakeGrok
from symphony_server.voices import VOICE_MODELS
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


def test_prompt_names_no_tools() -> None:
    for name in (*FUNCTIONS, "web_search"):
        assert name not in DEFAULT_INSTRUCTIONS


@pytest.mark.parametrize(
    ("model", "effort"),
    [(m.id, effort) for m in VOICE_MODELS for effort in m.capabilities.reasoningEffort or []],
)
def test_every_selectable_model_and_effort_gets_the_full_tool_list(
    client: TestClient, grok: FakeGrok, model: str, effort: str
) -> None:
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        ws.send_text(json.dumps({"type": "session.configure", "model": model, "voice": "sal", "effort": effort}))
        session = grok.next_of("session.update")["session"]
        assert session["voice"] == "sal" and session["reasoning"] == {"effort": effort}
        assert_native_tools(session)


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

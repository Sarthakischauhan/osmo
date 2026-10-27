"""OSMO_TOOLS categories, create_image, web_search over Exa, and the in-memory history."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from core_ai import ModelRegistry
from core_ai.types import Message
from core_harness import EventSink

from symphony_server.app import osmo_tools
from symphony_server.canvas import OSMO_WIDGET, CreateImageTool, WidgetBoard
from symphony_server.file_store import FileSecretStore
from symphony_server.memory import MemoryPersistence
from symphony_server.model_access import ModelAccess
from symphony_server.providers import CredentialStore
from symphony_server.settings import Settings, tool_categories
from symphony_server.web_search import EXA_MCP_URL, WebSearchTool

JPEG = b"\xff\xd8\xff\xe0fake"


class DrawingRegistry(ModelRegistry):
    """A registry whose image model answers with a canned JPEG and records the request."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[str, str | None]] = []

    async def generate_image(self, prompt: str, *, output_format: str = "png", model_id: str | None = None) -> tuple[bytes, str]:
        self.asked.append((prompt, model_id))
        return JPEG, "image/jpeg"


def tool_names(categories: str | None) -> set[str]:
    access = ModelAccess(ModelRegistry(), CredentialStore(FileSecretStore(None)), "http://unused/v1")
    settings = Settings(tool_categories=tool_categories(categories))
    return {tool.name for tool in osmo_tools(settings, httpx.AsyncClient(), access, WidgetBoard())}


def test_osmo_tools_follows_the_enabled_categories() -> None:
    osmo = {"render_widget", "update_widget", "set_voice", "get_weather", "get_stock_history"}
    assert tool_names(None) == osmo | {"web_search", "create_image"}
    assert tool_names("desktop") == {"computer"}
    assert tool_names("osmo") == osmo
    assert tool_names("search, image") == {"web_search", "create_image"}


def test_unknown_or_toolless_categories_are_refused() -> None:
    with pytest.raises(ValueError, match="no connect_browser"):
        tool_categories("osmo,browser")
    with pytest.raises(ValueError, match="unknown category 'maps'"):
        tool_categories("maps")


def test_create_image_draws_with_the_configured_model_and_shows_a_card() -> None:
    registry, sink = DrawingRegistry(), EventSink()
    tool = CreateImageTool(registry, WidgetBoard(), "grok:grok-imagine-image-2.0")
    result = json.loads(asyncio.run(tool.execute(sink=sink, args={"prompt": "a red fox", "title": "Fox"})))
    assert registry.asked == [("a red fox", "grok:grok-imagine-image-2.0")]
    [event] = [e for e in sink.events if e.event_type == OSMO_WIDGET]
    widget = event.payload["widget"]
    assert widget["props"]["image"].startswith("data:image/jpeg;base64,/9j/")
    assert result == {"shown": 'card "Fox"', "id": widget["id"]}


def test_web_search_calls_exa_and_returns_its_text() -> None:
    def exa(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == EXA_MCP_URL and request.headers["x-api-key"] == "exa-key"
        assert request.headers["content-type"] == "application/json"
        call = json.loads(request.content)
        assert call["params"]["arguments"]["query"] == "osmo"
        reply = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "Title: Osmo\nURL: https://x"}]}}
        return httpx.Response(200, text=f"event: message\ndata: {json.dumps(reply)}\n\n")

    tool = WebSearchTool(httpx.AsyncClient(transport=httpx.MockTransport(exa)), "exa-key")
    assert asyncio.run(tool.execute(sink=EventSink(), args={"query": "osmo"})) == "Title: Osmo\nURL: https://x"


def test_memory_persistence_keeps_only_the_most_recent_sessions() -> None:
    memory = MemoryPersistence(max_sessions=2)

    async def run() -> None:
        for session in ("a", "b", "a", "c"):
            await memory.save_conversation(session_id=session, messages=[Message(role="user", content=session)])
        assert list(memory.conversations) == ["a", "c"]
        assert await memory.load_conversation(session_id="b") == []

    asyncio.run(run())

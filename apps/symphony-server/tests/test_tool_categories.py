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
from symphony_server.browser_sessions import BrowserSessions
from symphony_server.browser_tools import BROWSER_TOOL_NAMES
from symphony_server.canvas import OSMO_WIDGET, CreateImageTool, WidgetBoard
from symphony_server.file_store import FileSecretStore
from symphony_server.memory import MemoryPersistence
from symphony_server.music import MUSIC_TOOL_NAMES, MusicNow, music_tools
from symphony_server.music_library import LocalLibrary
from symphony_server.spotify import SpotifyAccount
from symphony_server.model_access import ModelAccess
from symphony_server.providers import CredentialStore
from symphony_server.settings import Settings, tool_categories
from symphony_server.calendar_store import CalendarStore
from symphony_server.scheduler import Scheduler
from symphony_server.vault_index import VaultIndex
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
    board = WidgetBoard()
    calendar = CalendarStore(None)
    client = httpx.AsyncClient()
    music = music_tools(LocalLibrary(()), SpotifyAccount(None, access.credentials, client), MusicNow())
    tools = osmo_tools(settings, client, access, board, BrowserSessions(None, board), VaultIndex(()), calendar, Scheduler(calendar, VaultIndex(()), "UTC"), music)
    return {tool.name for tool in tools}


def test_osmo_tools_follows_the_enabled_categories() -> None:
    osmo = {"render_widget", "update_widget", "set_voice", "get_weather", "get_stock_history"}
    assert tool_names(None) == osmo | {"web_search", "create_image", "vault_search", "vault_read", "vault_write", "calendar_events", "calendar_add_event", "automation_create"} | MUSIC_TOOL_NAMES
    assert tool_names("music") == MUSIC_TOOL_NAMES == {"music_play", "music_control", "music_status"}
    assert tool_names("calendar") == {"calendar_events", "calendar_add_event", "automation_create"}
    assert tool_names("vault") == {"vault_search", "vault_read", "vault_write"}
    assert tool_names("desktop") == {"computer"}
    assert tool_names("browser") == BROWSER_TOOL_NAMES
    assert {"browser_back", "browser_forward", "browser_press", "browser_select", "browser_wait", "browser_click"} <= BROWSER_TOOL_NAMES
    assert tool_names("osmo") == osmo
    assert tool_names("search, image") == {"web_search", "create_image"}



def test_the_voice_model_does_quick_things_and_the_agent_long_ones() -> None:
    from symphony_server.app import AGENT_TOOLS, VOICE_TOOLS

    # No tool is on both sides. Search, notes, the calendar and music are the voice model's;
    # the computer, widgets and the browser are the agent's.
    assert VOICE_TOOLS & AGENT_TOOLS == set()
    assert AGENT_TOOLS == {"computer", "render_widget", "update_widget"} | BROWSER_TOOL_NAMES
    assert {"web_search", "vault_search", "vault_read", "vault_write", "calendar_events", "calendar_add_event", "automation_create"} <= VOICE_TOOLS


def test_unknown_categories_are_refused() -> None:
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

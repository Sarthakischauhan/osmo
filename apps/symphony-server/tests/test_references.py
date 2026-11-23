"""Read-only cross-reference tools and markdown issue discovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from symphony_server.reference_tools import (
    REFERENCE_TOOL_NAMES,
    AutomationListTool,
    ConversationReadTool,
    ConversationSearchTool,
    EventSearchTool,
    VaultIssuesTool,
    VaultListTool,
    issues,
)
from symphony_server.storage import SQLiteStorage
from symphony_server.calendar_store import CalendarStore
from symphony_server.vault_index import VaultIndex
from symphony_server.vault_paths import Vault
from symphony_server.settings import DEFAULT_APPROVAL_TOOLS, DEFAULT_TOOL_CATEGORIES, TOOL_CATEGORIES
from core_harness import EventSink


def index_for(tmp_path: Path) -> VaultIndex:
    root = tmp_path / "notes"
    root.mkdir()
    (root / "Planning.md").write_text(
        "# Planning\nstatus: doing\npriority: high\n\n"
        "- [ ] Fix transcription pipeline\n- [x] Test microphone\n"
        "[[chat:abc123]] [[automation:run2]] [[event:meeting3]]\n"
    )
    (root / "Reading.md").write_text("# Reading\nGeneral ideas, no task metadata.\n")
    index = VaultIndex((Vault("notes", root),))
    index.sync()
    index.ready.set()
    return index


def test_markdown_notes_are_usable_as_read_only_issues(tmp_path: Path) -> None:
    index = index_for(tmp_path)
    try:
        opened = issues(index, status="open")
        assert [i["task"] for i in opened] == ["Planning", "Fix transcription pipeline"]
        assert opened[0]["priority"] == "high"
        assert opened[1]["line"] == 5
        done = issues(index, status="done")
        assert [i["task"] for i in done] == ["Test microphone"]
        assert issues(index, status="all", limit=2) == (opened + done)[:2]
        assert index.read("Planning.md", "notes").text.startswith("# Planning")
    finally:
        index.close()


@pytest.mark.asyncio
async def test_vault_list_and_issues_are_available_without_write_approval(tmp_path: Path) -> None:
    index = index_for(tmp_path)
    try:
        sink = cast(EventSink, None)
        listed = json.loads(await VaultListTool(index).execute(sink=sink, args={}))
        assert [i["path"] for i in listed["notes"]] == ["Planning.md", "Reading.md"]
        found = json.loads(await VaultIssuesTool(index).execute(sink=sink, args={}))
        assert len(found["issues"]) == 2
        assert "vault_write" in DEFAULT_APPROVAL_TOOLS
        assert all(name in TOOL_CATEGORIES for name in REFERENCE_TOOL_NAMES)
        assert {"vault", "calendar", "osmo"}.issubset(DEFAULT_TOOL_CATEGORIES)
    finally:
        index.close()


@pytest.mark.asyncio
async def test_agent_can_search_and_refer_to_previous_osmo_chats(tmp_path: Path) -> None:
    storage = SQLiteStorage(None)
    sink = cast(EventSink, None)
    thread = storage.create_thread("Speech issues")
    storage.add_message(thread.id, "user", "Osmo sometimes misses words")
    result = json.loads(await ConversationSearchTool(storage).execute(sink=sink, args={"query": "misses words"}))
    assert result["conversations"][0]["threadId"] == thread.id
    detail = json.loads(await ConversationReadTool(storage).execute(sink=sink, args={"thread_id": thread.id}))
    assert detail["messages"][-1]["text"] == "Osmo sometimes misses words"
    assert "error" in json.loads(await ConversationReadTool(storage).execute(sink=sink, args={"thread_id": "missing"}))


@pytest.mark.asyncio
async def test_calendar_reference_tools_are_read_only(tmp_path: Path) -> None:
    store = CalendarStore(None)
    sink = cast(EventSink, None)
    automations = json.loads(await AutomationListTool(store).execute(sink=sink, args={}))
    events = json.loads(await EventSearchTool(store).execute(sink=sink, args={}))
    assert automations["automations"] == []
    assert events["events"] == []

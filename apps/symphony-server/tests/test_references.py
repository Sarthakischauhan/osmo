"""Read-only cross-reference tools and markdown issue discovery."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from symphony_server.reference_tools import (
    REFERENCE_TOOL_NAMES,
    AutomationListTool,
    ConversationReadTool,
    ConversationSearchTool,
    EventSearchTool,
    VaultListTool,
)
from symphony_server.storage import SQLiteStorage
from symphony_server.calendar_store import CalendarStore
from symphony_server.vault_index import VaultIndex, frontmatter_tags
from symphony_server.vault_paths import Vault
from symphony_server.settings import DEFAULT_APPROVAL_TOOLS, DEFAULT_TOOL_CATEGORIES, TOOL_CATEGORIES
from core_harness import EventSink


def index_for(tmp_path: Path) -> VaultIndex:
    root = tmp_path / "notes"
    root.mkdir()
    (root / "Planning.md").write_text(
        "---\ntags: [osmo, agent, bugs]\nstatus: doing\npriority: high\n---\n# Planning\n"
        "- [ ] Fix transcription pipeline\n- [x] Test microphone\n"
        "[[chat:abc123]] [[automation:run2]] [[event:meeting3]]\n"
    )
    (root / "Reading.md").write_text("# Reading\nGeneral ideas, no task metadata.\n")
    index = VaultIndex((Vault("notes", root),))
    index.sync()
    index.ready.set()
    return index


@pytest.mark.asyncio
async def test_vault_list_is_available_without_write_approval(tmp_path: Path) -> None:
    index = index_for(tmp_path)
    try:
        sink = cast(EventSink, None)
        listed = json.loads(await VaultListTool(index).execute(sink=sink, args={}))
        assert [i["path"] for i in listed["notes"]] == ["Planning.md", "Reading.md"]
        assert listed["notes"][0]["tags"] == ["osmo", "agent", "bugs"]
        assert "vault_write" in DEFAULT_APPROVAL_TOOLS
        assert all(name in TOOL_CATEGORIES for name in REFERENCE_TOOL_NAMES)
        assert {"vault", "calendar", "osmo"}.issubset(DEFAULT_TOOL_CATEGORIES)
    finally:
        index.close()


def test_frontmatter_tag_metadata_and_search(tmp_path: Path) -> None:
    assert frontmatter_tags("---\ntags: [one, two, one]\n---\n# A") == ["one", "two"]
    assert frontmatter_tags("---\ntags:\n  - voice\n  - agent\n---\n# B") == ["voice", "agent"]
    assert frontmatter_tags("# A\ntags: hidden") == []
    index = index_for(tmp_path)
    try:
        assert index.read("Planning.md").tags == ["osmo", "agent", "bugs"]
        assert index.entries("notes")[0].tags == ["osmo", "agent", "bugs"]
        assert index.search("osmo")[0].tags == ["osmo", "agent", "bugs"]
    finally:
        index.close()


def test_legacy_vault_index_reindexes_tags_on_migration(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Work.md").write_text("---\ntags: [planning]\n---\n# Work\n")
    db_file = tmp_path / "index.sqlite"
    with sqlite3.connect(db_file) as db:
        db.execute("CREATE TABLE notes (vault TEXT NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL, PRIMARY KEY (vault, path))")
        db.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?)", ("notes", "Work.md", "Work", 0.0, 10))
    index = VaultIndex((Vault("notes", root),), db_file)
    try:
        index.sync()
        assert index.entries()[0].tags == ["planning"]
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

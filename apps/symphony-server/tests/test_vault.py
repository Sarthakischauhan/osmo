"""Note vaults: path containment, the FTS index and its watcher, the tools, the editor API and approval gating."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from core_harness import EventSink
from fastapi.testclient import TestClient
from watchfiles import Change

from conftest import FakeGrok, make_client, xai_key_store
from test_session import call, next_event, say, start, tool_output
from symphony_server.settings import DEFAULT_APPROVAL_TOOLS, Settings
from symphony_server.vault_index import VaultIndex, fts_query
from symphony_server.vault_paths import Vault, VaultError, parse_vaults, resolve_note
from symphony_server.vault_tools import VaultReadTool, VaultSearchTool, VaultWriteTool


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "notes"
    (root / "Projects").mkdir(parents=True)
    (root / "Projects" / "osmo.md").write_text("# Osmo roadmap\n\nShip the vault editor. See [[groceries]].\n")
    (root / "groceries.md").write_text("Oat milk, lemons, basil\n")
    return Vault("notes", root)


@pytest.fixture
def index(vault: Vault) -> VaultIndex:
    built = VaultIndex((vault,))
    built.sync()
    built.ready.set()
    return built


# ------------------------------------------------------------ containment


def test_paths_inside_the_vault_resolve(vault: Vault) -> None:
    assert resolve_note(vault.root, "Projects/osmo.md") == (vault.root / "Projects" / "osmo.md").resolve()
    assert resolve_note(vault.root, "new/folder/idea.md") == (vault.root / "new" / "folder" / "idea.md").resolve()


@pytest.mark.parametrize(
    "path, reason",
    [
        ("/etc/passwd.md", "absolute"),
        ("C:/Users/x.md", "absolute"),
        ("../outside.md", "leaves the vault"),
        ("Projects/../../outside.md", "leaves the vault"),
        ("Projects\\..\\..\\outside.md", "leaves the vault"),
        ("notes.txt", "not a markdown"),
        (".obsidian/workspace.md", "hidden"),
        ("", "required"),
        ("a\0b.md", "required"),
    ],
)
def test_paths_that_escape_or_are_not_notes_are_refused(vault: Vault, path: str, reason: str) -> None:
    with pytest.raises(VaultError, match=reason):
        resolve_note(vault.root, path)


def test_a_symlinked_folder_out_of_the_vault_is_refused(vault: Vault, tmp_path: Path) -> None:
    outside = tmp_path / "secrets"
    outside.mkdir()
    (outside / "keys.md").write_text("hunter2")
    (vault.root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(VaultError, match="outside the vault"):
        resolve_note(vault.root, "linked/keys.md")


def test_a_symlinked_note_out_of_the_vault_is_refused_for_reads_writes_and_the_index(vault: Vault, tmp_path: Path) -> None:
    secret = tmp_path / "secret.md"
    secret.write_text("hunter2")
    (vault.root / "pointer.md").symlink_to(secret)
    index = VaultIndex((vault,))
    index.sync()
    index.ready.set()
    with pytest.raises(VaultError, match="outside the vault"):
        index.read("pointer.md")
    with pytest.raises(VaultError, match="outside the vault"):
        index.write("pointer.md", "overwritten", "replace")
    assert secret.read_text() == "hunter2"
    assert [entry.path for entry in index.entries()] == ["Projects/osmo.md", "groceries.md"]
    assert index.search("hunter2") == []


def test_a_symlink_that_stays_inside_the_vault_is_followed(vault: Vault) -> None:
    (vault.root / "alias.md").symlink_to(vault.root / "groceries.md")
    assert "lemons" in VaultIndex((vault,)).read("alias.md").text


# ------------------------------------------------------------ index, search, read and write


def test_search_finds_notes_by_title_and_body_with_a_snippet(index: VaultIndex) -> None:
    [hit] = index.search("vault edit")
    assert (hit.path, hit.title) == ("Projects/osmo.md", "Osmo roadmap")
    assert "[vault]" in hit.snippet
    assert [h.path for h in index.search("lemon")] == ["groceries.md"]


def test_search_takes_any_text_without_fts_syntax_errors(index: VaultIndex) -> None:
    assert fts_query('"quoted" AND -minus: (paren') == '"quoted" "AND" "minus" "paren"*'
    assert fts_query("  ...  ") is None
    assert [h.path for h in index.search('(roadmap" -')] == ["Projects/osmo.md"]


def test_read_returns_the_text_and_the_notes_that_link_to_it(index: VaultIndex) -> None:
    note = index.read("groceries.md")
    assert note.title == "groceries" and "basil" in note.text
    assert note.backlinks == ["Projects/osmo.md"]


def test_write_creates_appends_and_replaces_and_reindexes(index: VaultIndex, vault: Vault) -> None:
    index.write("Inbox/today.md", "# Today\n\nCall the dentist", "create")
    assert [h.path for h in index.search("dentist")] == ["Inbox/today.md"]
    with pytest.raises(VaultError, match="already exists"):
        index.write("Inbox/today.md", "again", "create")
    index.write("Inbox/today.md", "Buy stamps", "append")
    assert (vault.root / "Inbox" / "today.md").read_text() == "# Today\n\nCall the dentist\nBuy stamps"
    index.write("Inbox/today.md", "# Today\n\nNothing left", "replace")
    assert index.search("dentist") == []
    assert [h.path for h in index.search("nothing left")] == ["Inbox/today.md"]


def test_sync_drops_deleted_notes_and_picks_up_new_ones(index: VaultIndex, vault: Vault) -> None:
    (vault.root / "groceries.md").unlink()
    (vault.root / "ideas.md").write_text("A walking desk")
    assert index.sync() == 2
    assert [entry.path for entry in index.entries()] == ["Projects/osmo.md", "ideas.md"]
    assert index.sync() == 0


def test_watcher_batches_reindex_only_notes_inside_a_vault(index: VaultIndex, vault: Vault, tmp_path: Path) -> None:
    (vault.root / "fresh.md").write_text("Pelicans at dawn")
    (tmp_path / "elsewhere.md").write_text("Pelicans elsewhere")
    applied = index.apply([(Change.added, str(vault.root.resolve() / "fresh.md")), (Change.added, str(tmp_path / "elsewhere.md"))])
    assert applied == 1
    assert [h.path for h in index.search("pelicans")] == ["fresh.md"]
    (vault.root / "fresh.md").unlink()
    index.apply([(Change.deleted, str(vault.root.resolve() / "fresh.md"))])
    assert index.search("pelicans") == []


async def test_the_file_watcher_reindexes_a_note_saved_outside_osmo(index: VaultIndex, vault: Vault) -> None:
    stop = asyncio.Event()
    watching = asyncio.create_task(index.watch(stop))
    await asyncio.sleep(0.3)
    (vault.root / "outside-edit.md").write_text("Flamingo sightings")
    try:
        async with asyncio.timeout(10):
            while not index.search("flamingo"):
                await asyncio.sleep(0.1)
    finally:
        stop.set()
        await asyncio.wait_for(watching, 5)
    assert [h.path for h in index.search("flamingo")] == ["outside-edit.md"]


def test_parse_vaults() -> None:
    assert parse_vaults(None, Path("/n")) == (Vault("notes", Path("/n")),)
    assert parse_vaults("", Path("/n")) == ()
    assert parse_vaults("work=/w, home=/h", Path("/n")) == (Vault("work", Path("/w")), Vault("home", Path("/h")))
    with pytest.raises(ValueError, match="not name=path"):
        parse_vaults("/just/a/path", Path("/n"))
    with pytest.raises(ValueError, match="listed twice"):
        parse_vaults("a=/x,a=/y", Path("/n"))


# ------------------------------------------------------------ tools


def test_tools_search_read_and_write(index: VaultIndex) -> None:
    sink = EventSink()
    found = json.loads(asyncio.run(VaultSearchTool(index).execute(sink=sink, args={"query": "roadmap"})))
    assert [r["path"] for r in found["results"]] == ["Projects/osmo.md"]
    read = json.loads(asyncio.run(VaultReadTool(index).execute(sink=sink, args={"path": "Projects/osmo.md"})))
    assert read["title"] == "Osmo roadmap"
    escaped = json.loads(asyncio.run(VaultReadTool(index).execute(sink=sink, args={"path": "../../etc/hosts.md"})))
    assert "leaves the vault" in escaped["error"]
    written = json.loads(asyncio.run(VaultWriteTool(index).execute(sink=sink, args={"path": "a.md", "text": "hi", "mode": "create"})))
    assert written == {"written": "a.md", "vault": "notes", "mode": "create"}
    unknown = json.loads(asyncio.run(VaultSearchTool(index).execute(sink=sink, args={"query": "x", "vault": "work"})))
    assert "no vault named 'work'" in unknown["error"]


def test_vault_write_waits_for_approval_by_default() -> None:
    assert "vault_write" in DEFAULT_APPROVAL_TOOLS
    assert "vault_read" not in DEFAULT_APPROVAL_TOOLS and "vault_search" not in DEFAULT_APPROVAL_TOOLS


# ------------------------------------------------------------ the editor's API and the voice approval gate


def test_the_editor_lists_reads_saves_and_refuses_escapes(vault: Vault) -> None:
    with make_client(Settings(vaults=(vault,), app_token="launch-secret")) as client:
        client.headers["X-Osmo-Desktop-Token"] = "launch-secret"
        assert client.get("/api/vault").json() == {"vaults": [{"name": "notes", "root": str(vault.root)}]}
        notes = client.get("/api/vault/notes").json()["notes"]
        assert [n["path"] for n in notes] == ["Projects/osmo.md", "groceries.md"]
        saved = client.put("/api/vault/note", json={"path": "groceries.md", "text": "Figs", "mode": "replace"})
        assert saved.status_code == 200 and saved.json()["text"] == "Figs"
        assert (vault.root / "groceries.md").read_text() == "Figs"
        assert client.get("/api/vault/search", params={"q": "figs"}).json()["results"][0]["path"] == "groceries.md"
        refused = client.get("/api/vault/note", params={"path": "../escape.md"})
        assert refused.status_code == 400 and "leaves the vault" in refused.json()["detail"]
        assert client.put("/api/vault/note", json={"path": "/tmp/x.md", "text": "x"}).status_code == 400


def voice_write(client: TestClient, grok: FakeGrok, approve: bool) -> tuple[dict[str, object], dict[str, object]]:
    """The voice model calls vault_write; the user answers the approval card; the tool's result and the card."""
    with client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "add milk to my groceries")
        call(grok, "vault_write", {"path": "groceries.md", "text": "Milk", "mode": "append"}, "c1")
        request = next_event(ws, "approval.request")
        assert request["runId"] == "voice" and request["tool"] == "vault_write"
        assert request["summary"] == "Adding to groceries.md"
        assert client.post(f"/api/approvals/{request['approvalId']}", json={"approved": approve}).status_code == 204
        resolved = next_event(ws, "approval.resolved")
        assert resolved["approved"] is approve
        return tool_output(grok), request


def test_a_voice_vault_write_runs_only_after_the_user_approves(grok: FakeGrok, vault: Vault) -> None:
    with make_client(Settings(xai_base_url=grok.base_url, vaults=(vault,)), secret_store=xai_key_store()) as client:
        output, _ = voice_write(client, grok, approve=True)
    assert output == {"written": "groceries.md", "vault": "notes", "mode": "append"}
    assert (vault.root / "groceries.md").read_text() == "Oat milk, lemons, basil\nMilk"


def test_a_denied_voice_vault_write_changes_nothing(grok: FakeGrok, vault: Vault) -> None:
    before = (vault.root / "groceries.md").read_text()
    with make_client(Settings(xai_base_url=grok.base_url, vaults=(vault,)), secret_store=xai_key_store()) as client:
        output, _ = voice_write(client, grok, approve=False)
    assert "did not approve vault_write" in str(output["error"])
    assert (vault.root / "groceries.md").read_text() == before


def test_reads_need_no_approval_on_voice(grok: FakeGrok, vault: Vault) -> None:
    with make_client(Settings(xai_base_url=grok.base_url, vaults=(vault,)), secret_store=xai_key_store()) as client:
        with client.websocket_connect("/ws") as ws:
            start(ws, grok)
            say(grok, "what is on my grocery list")
            call(grok, "vault_read", {"path": "groceries.md"}, "c1")
            assert "basil" in tool_output(grok)["text"]
    assert os.path.exists(vault.root / "groceries.md")

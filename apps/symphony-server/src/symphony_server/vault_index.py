"""The vaults' search index: an SQLite FTS5 index of every note (title, body, wiki-links), and reading and writing notes.

`VaultIndex` keeps an SQLite FTS5 index of every note (title, body, wiki-links) that `sync` brings up to date by
mtime and size, and `watch` keeps fresh from a file watcher (watchfiles). The agent and voice tools search, read
and write through it; `vault_write` is an approval tool (settings.DEFAULT_APPROVAL_TOOLS), so neither the agent
nor the voice model changes a note without the user's OK. The user's own edits in the editor (`PUT
/api/vault/note`) need none.
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel
from watchfiles import Change, awatch

from symphony_server.vault_paths import NOTE_BYTES_MAX, NOTE_SUFFIX, SKIPPED_DIRS, Vault, VaultError, WriteMode, ends_without_newline, open_note, relative_name, resolve_note


SEARCH_LIMIT_MAX = 25
WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
HEADING = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)


def note_title(relative: str, text: str) -> str:
    """A note's title: its first `# heading`, else its file name."""
    heading = HEADING.search(text)
    return heading.group(1).strip() if heading else PurePosixPath(relative).stem


def note_links(text: str) -> list[str]:
    """The distinct `[[wiki-link]]` targets in a note, sorted."""
    return sorted({link.strip() for link in WIKI_LINK.findall(text) if link.strip()})


def fts_query(text: str) -> str | None:
    """User words as an FTS5 query: each word quoted (so `-`, `:` and quotes are plain text), the last a prefix."""
    words = [word for word in re.findall(r"\w+", text, flags=re.UNICODE)]
    if not words:
        return None
    quoted = ['"' + word.replace('"', '""') + '"' for word in words]
    quoted[-1] += "*"
    return " ".join(quoted)


class NoteHit(BaseModel):
    """One search result: where the note is, its title and the matching snippet."""

    vault: str
    path: str
    title: str
    snippet: str


class NoteEntry(BaseModel):
    """One note in a vault listing."""

    vault: str
    path: str
    title: str
    modified: float
    size: int


class Note(BaseModel):
    """A whole note as read (cut at READ_CHARS_MAX for the model), with its links and backlinks."""

    vault: str
    path: str
    title: str
    text: str
    modified: float
    backlinks: list[str] = []


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    vault TEXT NOT NULL,
    path TEXT NOT NULL,
    title TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    PRIMARY KEY (vault, path)
);
CREATE TABLE IF NOT EXISTS links (
    vault TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS links_target ON links (vault, target);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5 (
    vault UNINDEXED, path UNINDEXED, title, body, tokenize = 'porter unicode61'
);
"""


class VaultIndex:
    """The vaults and the FTS5 index over their notes. Thread-safe: the watcher and syncs run in worker threads."""

    def __init__(self, vaults: Iterable[Vault], index_file: Path | None = None) -> None:
        self.vaults = {vault.name: vault for vault in vaults}
        if index_file is not None:
            index_file.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(index_file) if index_file else ":memory:", check_same_thread=False)
        self.lock = threading.Lock()
        # Set once the first sync after start is done: lists and searches wait for it rather than show half a vault.
        self.ready = asyncio.Event()
        with self.transaction() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """The database under the index's lock, committed on success and rolled back on error."""
        with self.lock, self.db:
            yield self.db

    def close(self) -> None:
        """Close the database."""
        self.db.close()

    def vault(self, name: str | None) -> Vault:
        """The vault named `name` (the first one when None); VaultError when there is none."""
        if name is None:
            if not self.vaults:
                raise VaultError("no vault is configured (OSMO_VAULTS)")
            return next(iter(self.vaults.values()))
        vault = self.vaults.get(name)
        if vault is None:
            raise VaultError(f"no vault named {name!r}; vaults: {', '.join(self.vaults) or 'none'}")
        return vault

    def ensure_roots(self) -> None:
        """Create every vault's root folder that does not exist yet."""
        for vault in self.vaults.values():
            vault.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ indexing

    def note_files(self, vault: Vault) -> Iterator[Path]:
        """Every .md file under the root, not following symlinked folders out and skipping hidden ones."""
        root = vault.root.resolve()
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if name not in SKIPPED_DIRS and not name.startswith(".")]
            for name in files:
                if not name.lower().endswith(NOTE_SUFFIX) or name.startswith("."):
                    continue
                path = Path(folder) / name
                try:
                    resolve_note(root, relative_name(root, path))
                except (VaultError, ValueError):
                    continue
                yield path

    def sync(self) -> int:
        """Bring the index up to date with the disk (by mtime and size); how many notes were (re)indexed or dropped."""
        changed = 0
        for vault in self.vaults.values():
            if not vault.root.is_dir():
                continue
            root = vault.root.resolve()
            with self.transaction() as db:
                known = {row[0]: (row[1], row[2]) for row in db.execute("SELECT path, mtime, size FROM notes WHERE vault = ?", (vault.name,))}
            seen: set[str] = set()
            for path in self.note_files(vault):
                relative = relative_name(root, path)
                seen.add(relative)
                stat = path.stat()
                if known.get(relative) != (stat.st_mtime, stat.st_size):
                    self.index_file(vault, relative)
                    changed += 1
            for gone in set(known) - seen:
                self.drop(vault, gone)
                changed += 1
        return changed

    def index_file(self, vault: Vault, relative: str) -> None:
        """(Re)index one note; drops it when it is gone or no longer a note inside the vault."""
        try:
            with os.fdopen(open_note(vault.root, relative, os.O_RDONLY), "rb") as handle:
                stat = os.fstat(handle.fileno())
                text = handle.read(NOTE_BYTES_MAX).decode("utf-8", errors="replace")
        except (VaultError, OSError):
            self.drop(vault, relative)
            return
        title = note_title(relative, text)
        with self.transaction() as db:
            db.execute("DELETE FROM notes_fts WHERE vault = ? AND path = ?", (vault.name, relative))
            db.execute("DELETE FROM links WHERE vault = ? AND source = ?", (vault.name, relative))
            db.execute(
                "INSERT OR REPLACE INTO notes (vault, path, title, mtime, size) VALUES (?, ?, ?, ?, ?)",
                (vault.name, relative, title, stat.st_mtime, stat.st_size),
            )
            db.execute("INSERT INTO notes_fts (vault, path, title, body) VALUES (?, ?, ?, ?)", (vault.name, relative, title, text))
            db.executemany("INSERT INTO links (vault, source, target) VALUES (?, ?, ?)", [(vault.name, relative, link) for link in note_links(text)])

    def drop(self, vault: Vault, relative: str) -> None:
        """Remove a note from the index."""
        with self.transaction() as db:
            db.execute("DELETE FROM notes WHERE vault = ? AND path = ?", (vault.name, relative))
            db.execute("DELETE FROM notes_fts WHERE vault = ? AND path = ?", (vault.name, relative))
            db.execute("DELETE FROM links WHERE vault = ? AND source = ?", (vault.name, relative))

    def apply(self, changes: Iterable[tuple[Change, str]]) -> int:
        """Reindex the notes a watcher batch touched (absolute paths); paths outside every vault are ignored."""
        applied = 0
        for _, changed in changes:
            path = Path(changed)
            for vault in self.vaults.values():
                root = vault.root.resolve()
                if not path.is_relative_to(root):
                    continue
                relative = path.relative_to(root).as_posix()
                if relative.lower().endswith(NOTE_SUFFIX):
                    self.index_file(vault, relative)
                    applied += 1
        return applied

    async def watch(self, stop: asyncio.Event | None = None) -> None:
        """Keep the index fresh from file-system events until cancelled (or `stop` is set)."""
        roots = [vault.root.resolve() for vault in self.vaults.values() if vault.root.is_dir()]
        if not roots:
            return
        async for changes in awatch(*roots, stop_event=stop, watch_filter=lambda _, path: path.lower().endswith(NOTE_SUFFIX)):
            await asyncio.to_thread(self.apply, changes)

    # ------------------------------------------------------------ what the tools and the editor do

    def search(self, query: str, vault: str | None = None, limit: int = 10) -> list[NoteHit]:
        """Notes matching `query` (full-text, best first), in one vault or all."""
        if vault is not None:
            self.vault(vault)
        match = fts_query(query)
        if match is None:
            return []
        limit = max(1, min(limit, SEARCH_LIMIT_MAX))
        sql = (
            "SELECT vault, path, title, snippet(notes_fts, 3, '[', ']', '…', 16) FROM notes_fts "
            "WHERE notes_fts MATCH ?" + (" AND vault = ?" if vault else "") + " ORDER BY bm25(notes_fts, 0, 0, 5.0, 1.0) LIMIT ?"
        )
        params: tuple[Any, ...] = (match, vault, limit) if vault else (match, limit)
        with self.transaction() as db:
            rows = db.execute(sql, params).fetchall()
        return [NoteHit(vault=row[0], path=row[1], title=row[2], snippet=row[3]) for row in rows]

    def entries(self, vault: str | None = None) -> list[NoteEntry]:
        """Every indexed note of a vault, by path."""
        name = self.vault(vault).name
        with self.transaction() as db:
            rows = db.execute("SELECT path, title, mtime, size FROM notes WHERE vault = ? ORDER BY path", (name,)).fetchall()
        return [NoteEntry(vault=name, path=row[0], title=row[1], modified=row[2], size=row[3]) for row in rows]

    def backlinks(self, vault: Vault, relative: str) -> list[str]:
        """The notes that link to `relative` by its name."""
        stem = PurePosixPath(relative).stem
        with self.transaction() as db:
            rows = db.execute(
                "SELECT DISTINCT source FROM links WHERE vault = ? AND (target = ? OR target = ?) AND source != ? ORDER BY source",
                (vault.name, stem, relative.removesuffix(NOTE_SUFFIX), relative),
            ).fetchall()
        return [row[0] for row in rows]

    def read(self, relative: str, vault: str | None = None) -> Note:
        """The note `relative` of a vault, read through `open_note`; VaultError when it cannot be."""
        chosen = self.vault(vault)
        path = resolve_note(chosen.root, relative)
        try:
            with os.fdopen(open_note(chosen.root, relative, os.O_RDONLY), "rb") as handle:
                status = os.fstat(handle.fileno())
                if status.st_size > NOTE_BYTES_MAX:
                    raise VaultError(f"{relative!r} is larger than {NOTE_BYTES_MAX // 1000} kB")
                text = handle.read().decode("utf-8", errors="replace")
        except FileNotFoundError as exc:
            raise VaultError(f"no note {relative!r} in vault {chosen.name!r}") from exc
        clean = relative_name(chosen.root, path)
        return Note(vault=chosen.name, path=clean, title=note_title(clean, text), text=text, modified=status.st_mtime, backlinks=self.backlinks(chosen, clean))

    def write(self, relative: str, text: str, mode: WriteMode, vault: str | None = None) -> Note:
        """Create, replace or append to a note, opened through `open_note` (no symlink anywhere, no hard link)."""
        chosen = self.vault(vault)
        if len(text.encode("utf-8")) > NOTE_BYTES_MAX:
            raise VaultError(f"a note may be at most {NOTE_BYTES_MAX // 1000} kB")
        path = resolve_note(chosen.root, relative)
        flags = os.O_RDWR | os.O_CREAT
        if mode == "create":
            flags |= os.O_EXCL
        elif mode == "replace":
            flags |= os.O_TRUNC
        else:
            flags |= os.O_APPEND
        try:
            fd = open_note(chosen.root, relative, flags)
        except FileExistsError as exc:
            raise VaultError(f"{relative!r} already exists; replace or append to it instead") from exc
        except OSError as exc:
            raise VaultError(f"could not write {relative!r}: {exc.strerror}") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if mode == "append" and ends_without_newline(fd) and not text.startswith("\n"):
                handle.write("\n")
            handle.write(text)
        clean = relative_name(chosen.root, path)
        self.index_file(chosen, clean)
        return self.read(clean, chosen.name)

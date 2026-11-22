"""Osmo's durable state: threads, their messages, agent runs, model usage and provider connections.

Callers use the `Storage` protocol; `SQLiteStorage` is the one implementation today, so a
Postgres store can replace it without touching them. Provider secrets never come here (they
stay in the SecretStore); a connection row records only which provider is connected, how, and when.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from symphony_server.secret_store import AuthKind

MessageRole = Literal["user", "assistant", "agent"]
AgentRunStatus = Literal["running", "waiting_approval", "completed", "failed", "cancelled"]
UsageSource = Literal["voice", "agent"]
DEFAULT_THREAD_TITLE = "New conversation"


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ThreadRecord(_Row):
    id: str
    title: str
    createdAt: float
    updatedAt: float
    forkedFrom: str | None = None


class MessageRecord(_Row):
    id: int
    threadId: str
    role: MessageRole
    text: str
    createdAt: float


class AgentRunRecord(_Row):
    runId: str
    threadId: str
    task: str
    status: AgentRunStatus
    result: str | None = None
    createdAt: float
    updatedAt: float


class UsageRecord(_Row):
    """One model response's tokens and price. `cachedTokens` is None when the provider or event did
    not report it, `costUsd` when the model has no catalog price; neither is ever guessed.
    `estimated` rows are the harness's token estimate (priced at catalog rates) and say so."""

    threadId: str
    runId: str | None = None
    source: UsageSource
    model: str
    inputTokens: int
    cachedTokens: int | None = None
    outputTokens: int
    reasoningTokens: int = 0
    costUsd: float | None = None
    # The provider reported no usage, so the harness estimated the tokens from the text.
    estimated: bool = False
    createdAt: float


class ProviderConnection(_Row):
    provider: str
    kind: AuthKind
    connectedAt: float


class Storage(Protocol):
    """What the server persists. Every method is synchronous and quick (a local transaction)."""

    def create_thread(self, title: str = DEFAULT_THREAD_TITLE) -> ThreadRecord: ...

    def get_thread(self, thread_id: str) -> ThreadRecord | None: ...

    def list_threads(self) -> list[ThreadRecord]: ...

    def rename_thread(self, thread_id: str, title: str) -> ThreadRecord | None: ...

    def fork_thread(self, thread_id: str, through_message: int | None = None) -> ThreadRecord | None: ...

    def delete_thread(self, thread_id: str) -> bool: ...

    def add_message(self, thread_id: str, role: MessageRole, text: str) -> MessageRecord | None: ...

    def messages(self, thread_id: str) -> list[MessageRecord]: ...

    def save_run(self, run: AgentRunRecord) -> None: ...

    def get_run(self, run_id: str) -> AgentRunRecord | None: ...

    def runs(self, thread_id: str) -> list[AgentRunRecord]: ...

    def add_usage(self, usage: UsageRecord) -> None: ...

    def usage_since(self, since: float, thread_id: str | None = None, limit: int = 5000) -> list[UsageRecord]: ...

    def save_connection(self, provider: str, kind: AuthKind) -> None: ...

    def remove_connection(self, provider: str) -> None: ...

    def connections(self) -> list[ProviderConnection]: ...

    def interrupt_unfinished(self, reason: str, keep: frozenset[str] = frozenset()) -> int: ...

    def setting(self, key: str) -> str | None: ...

    def save_setting(self, key: str, value: str | None) -> None: ...


SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    forked_from TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_thread ON messages(thread_id, id);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    task TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_by_thread ON runs(thread_id, created_at);
-- No foreign key to threads: spend stays counted after its thread is deleted.
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    run_id TEXT,
    source TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    cached_tokens INTEGER,
    output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL,
    cost_usd REAL,
    estimated INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_by_time ON usage(created_at);
CREATE INDEX IF NOT EXISTS usage_by_thread ON usage(thread_id, created_at);
CREATE TABLE IF NOT EXISTS provider_connections (
    provider TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    connected_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def thread_row(row: sqlite3.Row) -> ThreadRecord:
    return ThreadRecord(
        id=row["id"], title=row["title"], createdAt=row["created_at"], updatedAt=row["updated_at"], forkedFrom=row["forked_from"]
    )


def message_row(row: sqlite3.Row) -> MessageRecord:
    return MessageRecord(id=row["id"], threadId=row["thread_id"], role=row["role"], text=row["text"], createdAt=row["created_at"])


def run_row(row: sqlite3.Row) -> AgentRunRecord:
    return AgentRunRecord(
        runId=row["run_id"],
        threadId=row["thread_id"],
        task=row["task"],
        status=row["status"],
        result=row["result"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def usage_row(row: sqlite3.Row) -> UsageRecord:
    return UsageRecord(
        threadId=row["thread_id"],
        runId=row["run_id"],
        source=row["source"],
        model=row["model"],
        inputTokens=row["input_tokens"],
        cachedTokens=row["cached_tokens"],
        outputTokens=row["output_tokens"],
        reasoningTokens=row["reasoning_tokens"],
        costUsd=row["cost_usd"],
        estimated=bool(row["estimated"]),
        createdAt=row["created_at"],
    )


class SQLiteStorage:
    """`Storage` in one SQLite file (or in memory for `None`). One connection, serialized by a lock:
    calls are short local transactions, made from the event loop and from tool threads alike."""

    def __init__(self, path: Path | None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(":memory:" if path is None else str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        # Outside any transaction: SQLite ignores this pragma inside one.
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock, self.db:
            yield self.db

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ threads

    def create_thread(self, title: str = DEFAULT_THREAD_TITLE) -> ThreadRecord:
        with self.transaction() as db:
            return self._insert_thread(db, title, None)

    def _insert_thread(self, db: sqlite3.Connection, title: str, forked_from: str | None) -> ThreadRecord:
        now = time.time()
        thread = ThreadRecord(id=f"t-{uuid.uuid4().hex}", title=title, createdAt=now, updatedAt=now, forkedFrom=forked_from)
        db.execute(
            "INSERT INTO threads (id, title, created_at, updated_at, forked_from) VALUES (?, ?, ?, ?, ?)",
            (thread.id, thread.title, now, now, forked_from),
        )
        return thread

    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return thread_row(row) if row is not None else None

    def list_threads(self) -> list[ThreadRecord]:
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM threads ORDER BY updated_at DESC, id").fetchall()
        return [thread_row(row) for row in rows]

    def rename_thread(self, thread_id: str, title: str) -> ThreadRecord | None:
        with self.transaction() as db:
            db.execute("UPDATE threads SET title = ?, updated_at = ? WHERE id = ?", (title, time.time(), thread_id))
            row = db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return thread_row(row) if row is not None else None

    def fork_thread(self, thread_id: str, through_message: int | None = None) -> ThreadRecord | None:
        """A new thread holding a copy of `thread_id`'s messages (up to `through_message`, if given)."""
        with self.transaction() as db:
            source = db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if source is None:
                return None
            fork = self._insert_thread(db, f"{source['title']} (fork)", thread_id)
            limit = through_message if through_message is not None else -1
            db.execute(
                "INSERT INTO messages (thread_id, role, text, created_at) "
                "SELECT ?, role, text, created_at FROM messages WHERE thread_id = ? AND (? < 0 OR id <= ?) ORDER BY id",
                (fork.id, thread_id, limit, limit),
            )
        return fork

    def delete_thread(self, thread_id: str) -> bool:
        with self.transaction() as db:
            return db.execute("DELETE FROM threads WHERE id = ?", (thread_id,)).rowcount > 0

    # ------------------------------------------------------------ messages

    def add_message(self, thread_id: str, role: MessageRole, text: str) -> MessageRecord | None:
        """Append to a thread; None when the thread is gone (deleted while a session was on it)."""
        now = time.time()
        with self.transaction() as db:
            if db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, thread_id)).rowcount == 0:
                return None
            cursor = db.execute(
                "INSERT INTO messages (thread_id, role, text, created_at) VALUES (?, ?, ?, ?)", (thread_id, role, text, now)
            )
            message_id = cursor.lastrowid
        assert message_id is not None
        return MessageRecord(id=message_id, threadId=thread_id, role=role, text=text, createdAt=now)

    def messages(self, thread_id: str) -> list[MessageRecord]:
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM messages WHERE thread_id = ? ORDER BY id", (thread_id,)).fetchall()
        return [message_row(row) for row in rows]

    # ------------------------------------------------------------ agent runs

    def save_run(self, run: AgentRunRecord) -> None:
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM threads WHERE id = ?", (run.threadId,)).fetchone() is None:
                return
            db.execute(
                "INSERT INTO runs (run_id, thread_id, task, status, result, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, result = excluded.result, updated_at = excluded.updated_at",
                (run.runId, run.threadId, run.task, run.status, run.result, run.createdAt, run.updatedAt),
            )

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return run_row(row) if row is not None else None

    def runs(self, thread_id: str) -> list[AgentRunRecord]:
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM runs WHERE thread_id = ? ORDER BY created_at, run_id", (thread_id,)).fetchall()
        return [run_row(row) for row in rows]

    def interrupt_unfinished(self, reason: str, keep: frozenset[str] = frozenset()) -> int:
        """Mark runs that are not executing any more as failed, except `keep` (the runs the durable
        backend resumed); the stored record must not keep saying that the others are running."""
        now = time.time()
        held = tuple(keep)
        excluded = f" AND run_id NOT IN ({', '.join('?' for _ in held)})" if held else ""
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE runs SET status = 'failed', result = ?, updated_at = ? WHERE status IN ('running', 'waiting_approval')" + excluded,
                (reason, now, *held),
            )
        return changed.rowcount

    # ------------------------------------------------------------ usage

    def add_usage(self, usage: UsageRecord) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO usage (thread_id, run_id, source, model, input_tokens, cached_tokens, output_tokens, reasoning_tokens, cost_usd, estimated, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    usage.threadId,
                    usage.runId,
                    usage.source,
                    usage.model,
                    usage.inputTokens,
                    usage.cachedTokens,
                    usage.outputTokens,
                    usage.reasoningTokens,
                    usage.costUsd,
                    int(usage.estimated),
                    usage.createdAt,
                ),
            )

    def usage_since(self, since: float, thread_id: str | None = None, limit: int = 5000) -> list[UsageRecord]:
        """Usage from `since` on (of one thread, when given), newest first, at most `limit` rows."""
        with self.transaction() as db:
            rows = db.execute(
                "SELECT * FROM usage WHERE created_at >= ? AND (? IS NULL OR thread_id = ?) ORDER BY created_at DESC, id DESC LIMIT ?",
                (since, thread_id, thread_id, limit),
            ).fetchall()
        return [usage_row(row) for row in rows]

    # ------------------------------------------------------------ provider connections

    def save_connection(self, provider: str, kind: AuthKind) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO provider_connections (provider, kind, connected_at) VALUES (?, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET kind = excluded.kind, connected_at = excluded.connected_at",
                (provider, kind, time.time()),
            )

    def remove_connection(self, provider: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM provider_connections WHERE provider = ?", (provider,))

    def connections(self) -> list[ProviderConnection]:
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM provider_connections ORDER BY provider").fetchall()
        return [ProviderConnection(provider=row["provider"], kind=row["kind"], connectedAt=row["connected_at"]) for row in rows]

    # ------------------------------------------------------------ settings

    def setting(self, key: str) -> str | None:
        with self.transaction() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def save_setting(self, key: str, value: str | None) -> None:
        """Store `value` under `key`; None removes it."""
        with self.transaction() as db:
            if value is None:
                db.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                db.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value)
                )

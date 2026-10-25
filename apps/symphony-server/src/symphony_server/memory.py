"""In-process conversation store for harness runs, keyed by voice session id.

Every voice turn is a core_server run on the same session id; the harness loads
that session's history from here and saves it back. Bounded: only the most
recently used sessions are kept, and nothing survives a restart.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, TypeVar

from core_ai import Message
from core_harness import Checkpoint

SESSIONS_MAX = 256
Value = TypeVar("Value")


def keep_recent(store: OrderedDict[str, Value], key: str, value: Value, limit: int) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > limit:
        store.popitem(last=False)


class MemoryPersistence:
    """core_harness `Persistence` held in memory (conversations and checkpoints; no journal)."""

    def __init__(self, max_sessions: int = SESSIONS_MAX) -> None:
        self.max_sessions = max_sessions
        self.conversations: OrderedDict[str, list[Message]] = OrderedDict()
        self.checkpoints: OrderedDict[str, Checkpoint] = OrderedDict()

    async def save_conversation(self, *, session_id: str, messages: list[Message]) -> None:
        keep_recent(self.conversations, session_id, list(messages), self.max_sessions)

    async def load_conversation(self, *, session_id: str) -> list[Message]:
        return list(self.conversations.get(session_id, []))

    async def load_resume(self, *, session_id: str) -> tuple[list[Message], list[Message]]:
        messages = await self.load_conversation(session_id=session_id)
        return messages, list(messages)

    async def save_checkpoint(self, *, checkpoint: Checkpoint) -> None:
        keep_recent(self.checkpoints, checkpoint.session_id, checkpoint, self.max_sessions)

    async def load_checkpoint(self, *, session_id: str) -> Checkpoint | None:
        return self.checkpoints.get(session_id)

    async def append_event(self, *, event_type: str, payload: dict[str, Any]) -> None:
        del event_type, payload

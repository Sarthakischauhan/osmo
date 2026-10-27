"""REST API over the stored threads, for the sidebar and home screen:
list, create, read (with messages and agent runs), rename, fork and delete."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from symphony_server.storage import DEFAULT_THREAD_TITLE, AgentRunRecord, MessageRecord, Storage, ThreadRecord

TITLE_MAX = 200
# A title must have something besides whitespace.
NON_BLANK = r"\S"


class ThreadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default=DEFAULT_THREAD_TITLE, min_length=1, max_length=TITLE_MAX, pattern=NON_BLANK)


class ThreadRename(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX, pattern=NON_BLANK)


class ThreadFork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Copy messages up to and including this one; all of them when unset.
    throughMessageId: int | None = None


class ThreadList(BaseModel):
    threads: list[ThreadRecord]


class ThreadDetail(BaseModel):
    thread: ThreadRecord
    messages: list[MessageRecord]
    runs: list[AgentRunRecord]


def found(thread: ThreadRecord | None, thread_id: str) -> ThreadRecord:
    if thread is None:
        raise HTTPException(404, f"no thread {thread_id!r}")
    return thread


def create_thread_router(storage: Storage) -> APIRouter:
    router = APIRouter(prefix="/api/threads")

    @router.get("")
    async def list_threads() -> ThreadList:
        return ThreadList(threads=storage.list_threads())

    @router.post("", status_code=201)
    async def create_thread(body: ThreadCreate) -> ThreadRecord:
        return storage.create_thread(body.title.strip() or DEFAULT_THREAD_TITLE)

    @router.get("/{thread_id}")
    async def read_thread(thread_id: str) -> ThreadDetail:
        thread = found(storage.get_thread(thread_id), thread_id)
        return ThreadDetail(thread=thread, messages=storage.messages(thread_id), runs=storage.runs(thread_id))

    @router.patch("/{thread_id}")
    async def rename_thread(thread_id: str, body: ThreadRename) -> ThreadRecord:
        return found(storage.rename_thread(thread_id, body.title.strip()), thread_id)

    @router.post("/{thread_id}/fork", status_code=201)
    async def fork_thread(thread_id: str, body: ThreadFork) -> ThreadRecord:
        return found(storage.fork_thread(thread_id, body.throughMessageId), thread_id)

    @router.delete("/{thread_id}", status_code=204)
    async def delete_thread(thread_id: str) -> None:
        if not storage.delete_thread(thread_id):
            raise HTTPException(404, f"no thread {thread_id!r}")

    return router

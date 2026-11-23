"""The editor's HTTP API for the vaults: list, search, read and save notes."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from symphony_server.local_access import require_app
from symphony_server.reference_tools import issues
from symphony_server.vault_index import Note, NoteEntry, NoteHit, VaultIndex
from symphony_server.vault_paths import VaultError


class VaultInfo(BaseModel):
    """A vault as the editor lists it."""

    name: str
    root: str


class VaultList(BaseModel):
    """The configured vaults."""

    vaults: list[VaultInfo]


class NoteList(BaseModel):
    """A vault's notes."""

    notes: list[NoteEntry]


class SearchResults(BaseModel):
    """Search results."""

    results: list[NoteHit]


class NoteSave(BaseModel):
    """The editor's save of a note."""

    model_config = ConfigDict(extra="forbid")
    vault: str | None = None
    path: str = Field(min_length=1)
    text: str
    # The editor creates a new note with `create` (refused if it exists) and saves an open one with `replace`.
    mode: Literal["create", "replace"] = "replace"


def create_vault_router(index: VaultIndex, *, writes_approved: bool) -> APIRouter:
    """The editor's view of the vaults: the user's own reads and edits, which need no approval.

    When vault_write needs approval (`writes_approved`), saving is the desktop app's alone (see local_access.require_app):
    otherwise the agent could write a note by calling this route instead of its tool."""
    router = APIRouter(prefix="/api/vault", tags=["vault"])

    def refused(exc: VaultError) -> HTTPException:
        """A 400 carrying the vault's reason."""
        return HTTPException(status_code=400, detail=str(exc))

    @router.get("")
    async def list_vaults() -> VaultList:
        """The configured vaults and their roots."""
        return VaultList(vaults=[VaultInfo(name=vault.name, root=str(vault.root)) for vault in index.vaults.values()])

    @router.get("/notes")
    async def list_notes(vault: str | None = None) -> NoteList:
        """The notes of a vault (once the first index sync is done)."""
        await index.ready.wait()
        try:
            return NoteList(notes=await asyncio.to_thread(index.entries, vault))
        except VaultError as exc:
            raise refused(exc) from exc

    @router.get("/search")
    async def search(q: str = Query(min_length=1), vault: str | None = None, limit: int = 20) -> SearchResults:
        """Search the vaults (once the first index sync is done)."""
        await index.ready.wait()
        try:
            return SearchResults(results=await asyncio.to_thread(index.search, q, vault, limit))
        except VaultError as exc:
            raise refused(exc) from exc

    @router.get("/issues")
    async def list_issues(vault: str | None = None, status: Literal["open", "done", "all"] = "open", limit: int = Query(100, ge=1, le=100)) -> dict[str, list[dict[str, object]]]:
        """Read-only issue tracker view over markdown checkboxes and note statuses."""
        await index.ready.wait()
        try:
            return {"issues": await asyncio.to_thread(issues, index, vault, status, limit)}
        except VaultError as exc:
            raise refused(exc) from exc

    @router.get("/note")
    async def read(path: str, vault: str | None = None) -> Note:
        """Read one note."""
        try:
            return await asyncio.to_thread(index.read, path, vault)
        except VaultError as exc:
            raise refused(exc) from exc

    @router.put("/note", dependencies=[Depends(require_app)] if writes_approved else [])
    async def save(body: NoteSave) -> Note:
        """Save a note from the editor."""
        try:
            return await asyncio.to_thread(index.write, body.path, body.text, body.mode, body.vault)
        except VaultError as exc:
            raise refused(exc) from exc

    return router

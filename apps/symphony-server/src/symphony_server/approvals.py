"""Approval for risky agent steps: the run waits on the user, who answers in the web app.

`ApprovalAddon` (one per agent run, from core_server's addon_factories) holds a configured risky
tool in `before_tool`: it emits `approval_required` on the run, waits on the shared
`ApprovalBroker`, emits `approval_resolved`, and denies the tool unless the user approved.
`POST /api/approvals/{id}` resolves it; the voice bridge shows the request from the run's events.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from core_harness import Addon
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from symphony_server.turn_events import activity_label

APPROVAL_TIMEOUT_S = 600.0
APPROVAL_REQUIRED = "approval_required"
APPROVAL_RESOLVED = "approval_resolved"
ARGUMENTS_SHOWN_MAX = 400

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


class ApprovalPayload(BaseModel):
    """The `approval_required` run event."""

    approval_id: str
    tool: str
    summary: str
    arguments: str


class ApprovalResolvedPayload(BaseModel):
    approval_id: str
    approved: bool


@dataclass
class PendingApproval:
    session_id: str
    answer: asyncio.Future[bool] = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class ApprovalBroker:
    """Approvals waiting on the user, by id, across every run."""

    def __init__(self) -> None:
        self.pending: dict[str, PendingApproval] = {}

    def open(self, session_id: str) -> str:
        approval_id = f"ap-{secrets.token_hex(8)}"
        self.pending[approval_id] = PendingApproval(session_id)
        return approval_id

    async def wait(self, approval_id: str, timeout_s: float) -> bool:
        """The user's answer; False on timeout or when the run is cancelled first."""
        try:
            async with asyncio.timeout(timeout_s):
                return await self.pending[approval_id].answer
        except TimeoutError:
            return False
        finally:
            self.pending.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool) -> bool:
        pending = self.pending.get(approval_id)
        if pending is None or pending.answer.done():
            return False
        pending.answer.set_result(approved)
        return True

    def deny_session(self, session_id: str) -> None:
        """Deny whatever a run (by its session) is still waiting on, e.g. when it is cancelled."""
        for pending in self.pending.values():
            if pending.session_id == session_id and not pending.answer.done():
                pending.answer.set_result(False)


def shown_arguments(arguments: Mapping[str, Any]) -> str:
    text = json.dumps(dict(arguments), ensure_ascii=False)
    return text if len(text) <= ARGUMENTS_SHOWN_MAX else text[: ARGUMENTS_SHOWN_MAX - 1] + "…"


class ApprovalAddon(Addon):
    """Holds each of `risky` tools until the user approves it."""

    name = "osmo_approvals"

    def __init__(self, broker: ApprovalBroker, session_id: str, risky: frozenset[str], timeout_s: float = APPROVAL_TIMEOUT_S) -> None:
        self.broker, self.session_id, self.risky, self.timeout_s = broker, session_id, risky, timeout_s

    async def before_tool(self, **payload: Any) -> str | None:
        tool_name = str(payload["tool_name"])
        if tool_name not in self.risky:
            return None
        arguments: Mapping[str, Any] = payload["arguments"]
        emit: Emit = payload["emit"]
        approval_id = self.broker.open(self.session_id)
        request = ApprovalPayload(
            approval_id=approval_id,
            tool=tool_name,
            summary=activity_label(tool_name, arguments),
            arguments=shown_arguments(arguments),
        )
        await emit(APPROVAL_REQUIRED, request.model_dump())
        approved = await self.broker.wait(approval_id, self.timeout_s)
        await emit(APPROVAL_RESOLVED, ApprovalResolvedPayload(approval_id=approval_id, approved=approved).model_dump())
        return None if approved else f"the user did not approve {tool_name}; do not retry it, report that it was skipped"


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool


def create_approval_router(broker: ApprovalBroker) -> APIRouter:
    router = APIRouter(prefix="/api/approvals")

    @router.post("/{approval_id}", status_code=204)
    async def decide(approval_id: str, body: ApprovalDecision) -> None:
        if not broker.resolve(approval_id, body.approved):
            raise HTTPException(404, f"no pending approval {approval_id!r}")

    return router

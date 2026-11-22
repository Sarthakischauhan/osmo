"""Approval for risky agent steps: the run waits on the user, who answers in the web app.

`ApprovalAddon` (one per agent run, from core_server's addon_factories) holds a configured risky
tool in `before_tool`: it emits `approval_required` on the run, waits on the shared
`ApprovalBroker`, emits `approval_resolved`, and denies the tool unless the user approved.
`POST /api/approvals/{id}` resolves it; the voice bridge shows the request from the run's events.
An `ApprovalPolicy` (the agent browser's per-domain rules) can let a step through without asking, or make one ask.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from core_harness import Addon
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from symphony_server.turn_events import activity_label

APPROVAL_TIMEOUT_S = 600.0
APPROVAL_REQUIRED = "approval_required"
APPROVAL_RESOLVED = "approval_resolved"

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


class ApprovalPayload(BaseModel):
    """The `approval_required` run event."""

    approval_id: str
    tool: str
    summary: str
    # Every argument, never cut: what is approved is exactly what runs. The card folds long ones behind their digest.
    arguments: str
    digest: str


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
    """A step's arguments as the approval card shows them: all of them, as JSON."""
    return json.dumps(dict(arguments), ensure_ascii=False)


def arguments_digest(shown: str) -> str:
    """The SHA-256 of the shown arguments, so two long cards that look alike can be told apart."""
    return hashlib.sha256(shown.encode()).hexdigest()


class ApprovalPolicy(Protocol):
    def decide(self, tool_name: str, arguments: Mapping[str, Any]) -> Literal["allow", "ask"] | None:
        """`allow` runs the step without asking, `ask` always asks, None leaves it to the `risky` list."""
        ...


class ApprovalAddon(Addon):
    """Holds each of `risky` tools until the user approves it (unless `policy` says otherwise for a step)."""

    name = "osmo_approvals"

    def __init__(
        self,
        broker: ApprovalBroker,
        session_id: str,
        risky: frozenset[str],
        timeout_s: float = APPROVAL_TIMEOUT_S,
        policy: ApprovalPolicy | None = None,
    ) -> None:
        self.broker, self.session_id, self.risky, self.timeout_s, self.policy = broker, session_id, risky, timeout_s, policy

    async def before_tool(self, **payload: Any) -> str | None:
        tool_name = str(payload["tool_name"])
        arguments: Mapping[str, Any] = payload["arguments"]
        decision = self.policy.decide(tool_name, arguments) if self.policy is not None else None
        if decision == "allow" or (decision is None and tool_name not in self.risky):
            return None
        emit: Emit = payload["emit"]
        approval_id = self.broker.open(self.session_id)
        shown = shown_arguments(arguments)
        request = ApprovalPayload(
            approval_id=approval_id,
            tool=tool_name,
            summary=activity_label(tool_name, arguments),
            arguments=shown,
            digest=arguments_digest(shown),
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

"""Model usage and cost: what each voice response and agent turn used, priced from core_ai's
catalog, stored per thread (storage.py `usage`), served at `GET /api/usage` and sent live as `usage`.

Agent turns carry the harness `usage` event (tokens plus `session_cost`, the turn's USD at catalog
rates, cache reads included since Symphony 0.0.7). The event has no cached-token count, so it stays
unknown here. Voice responses report input and output tokens only, with no audio/text or cached
split, so they are stored unpriced until core_ai's voice usage carries that detail.
"""

from __future__ import annotations

import time

from core_ai.models.pricing import model_price
from core_ai.voice import ResponseDone
from fastapi import APIRouter, Query
from pydantic import BaseModel

from symphony_server.storage import Storage, UsageRecord

DAY_S = 86_400
RANGE_DAYS_MAX = 90


class AgentUsagePayload(BaseModel):
    """The harness `usage` event's payload (core_harness/loop/stream.py)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    model_id: str = ""
    session_cost: float = 0.0
    estimated: bool = False


def agent_usage(thread_id: str, run_id: str, payload: AgentUsagePayload) -> UsageRecord:
    """An agent turn's usage. The harness prices an unknown model at $0, so cost is None without a catalog price."""
    return UsageRecord(
        threadId=thread_id,
        runId=run_id,
        source="agent",
        model=payload.model_id,
        inputTokens=payload.prompt_tokens,
        outputTokens=payload.completion_tokens,
        reasoningTokens=payload.reasoning_tokens,
        costUsd=payload.session_cost if model_price(payload.model_id) is not None else None,
        estimated=payload.estimated,
        createdAt=time.time(),
    )


def voice_usage(thread_id: str, model: str, done: ResponseDone) -> UsageRecord | None:
    """A voice response's tokens, unpriced; None when the provider reported none."""
    if done.input_tokens is None and done.output_tokens is None:
        return None
    return UsageRecord(
        threadId=thread_id,
        source="voice",
        model=model,
        inputTokens=done.input_tokens or 0,
        outputTokens=done.output_tokens or 0,
        createdAt=time.time(),
    )


class UsageList(BaseModel):
    """Usage records since `since` (epoch seconds), for the usage dialog."""

    since: float
    usage: list[UsageRecord]


def create_usage_router(storage: Storage) -> APIRouter:
    """`GET /api/usage`: what voice answers and agent turns used."""
    router = APIRouter(prefix="/api/usage")

    @router.get("")
    async def list_usage(days: int = Query(default=30, ge=1, le=RANGE_DAYS_MAX), thread: str | None = None) -> UsageList:
        """Usage of the last `days` days, for one thread or all of them."""
        since = time.time() - days * DAY_S
        return UsageList(since=since, usage=storage.usage_since(since, thread))

    return router

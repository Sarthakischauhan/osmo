"""Langfuse traces for a voice turn. Disabled unless both project keys are set.

One trace is one spoken turn. The conversation is the session, so later turns
group in Langfuse without one trace growing for the whole call. Audio stays
out: only transcripts, tool arguments and tool results are recorded.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from langfuse import Langfuse, propagate_attributes


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


_client: Langfuse | None = None


def client() -> Langfuse | None:
    """The process client, created after env is loaded. Missing keys trace nothing."""
    global _client
    if not enabled():
        return None
    if _client is None:
        _client = Langfuse()
    return _client


def flush() -> None:
    if _client is not None:
        _client.flush()


def model_name(model: Any) -> str:
    """The provider model id, without Osmo's namespace prefix."""
    full = getattr(model, "full_id", None) or getattr(model, "id", "")
    return str(full).split(":", 1)[-1]


def messages(instructions: str, history: list[tuple[Any, str]], user: str) -> list[dict[str, str]]:
    """Role-labelled messages, so the session view can connect turns."""
    prior = [{"role": "system", "content": instructions}]
    prior.extend({"role": role, "content": text} for role, text in history if text)
    prior.append({"role": "user", "content": user})
    return prior


@contextmanager
def turn(
    *,
    session_id: str | None,
    user_text: str,
    instructions: str,
    history: list[tuple[Any, str]],
    model: str,
    voice: str,
) -> Iterator[Any]:
    """A `voice-turn` span. Yields None when Langfuse is not configured."""
    langfuse = client()
    if langfuse is None:
        yield None
        return
    with langfuse.start_as_current_observation(
        as_type="agent",
        name="voice-turn",
        input=user_text,
        metadata={"voice": voice},
    ) as span:
        with propagate_attributes(
            session_id=session_id,
            tags=["voice"],
            metadata={"model": model},
        ):
            with span.start_as_current_observation(
                as_type="generation",
                name="respond",
                model=model,
                input=messages(instructions, history, user_text),
            ) as generation:
                yield span, generation


@contextmanager
def tool_call(name: str, arguments: dict[str, Any]) -> Iterator[Any]:
    """A tool observation nested under the active turn. No-op without a turn."""
    langfuse = client()
    if langfuse is None:
        yield None
        return
    with langfuse.start_as_current_observation(
        as_type="tool",
        name=name,
        input=arguments,
    ) as observation:
        yield observation

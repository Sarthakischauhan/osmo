"""Compatibility for realtime voice sessions:

1. Grok response.done `status_details` sent as a string ("unimplemented") instead of an object.
2. Realtime reasoning / thinking delta events (`response.reasoning_summary_text.delta`,
   `response.reasoning.delta`, `response.thinking.delta`) routed as `ReasoningDelta` on the response stream.
"""

from dataclasses import dataclass
import json
from types import MethodType
from typing import Any, Literal

from core_ai.voice.realtime_api import RealtimeApiSession

REASONING_EVENT_TYPES = frozenset(
    {
        "response.reasoning_summary_text.delta",
        "response.reasoning.delta",
        "response.thinking.delta",
    }
)


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    text: str
    type: Literal["reasoning_delta"] = "reasoning_delta"


def normalize_response_done(frame: str) -> str:
    try:
        event = json.loads(frame)
    except (ValueError, TypeError):
        return frame
    if not isinstance(event, dict) or event.get("type") != "response.done":
        return frame
    response = event.get("response")
    if isinstance(response, dict) and isinstance(response.get("status_details"), str):
        response["status_details"] = {"reason": response["status_details"]}
        return json.dumps(event)
    return frame


def extract_reasoning_delta(frame: str) -> tuple[str | None, str] | None:
    try:
        event = json.loads(frame)
    except (ValueError, TypeError):
        return None
    if not isinstance(event, dict) or event.get("type") not in REASONING_EVENT_TYPES:
        return None
    delta = event.get("delta")
    if not isinstance(delta, str) or not delta:
        return None
    response_id = event.get("response_id")
    return (response_id if isinstance(response_id, str) else None, delta)


def install_grok_compat(session: RealtimeApiSession) -> None:
    original = session._handle

    def handle(_session: Any, frame: str) -> None:
        reasoning = extract_reasoning_delta(frame)
        if reasoning is not None:
            response_id, delta = reasoning
            session._responses.route(response_id, ReasoningDelta(delta))  # type: ignore[arg-type]
            return
        original(normalize_response_done(frame))

    session._handle = MethodType(handle, session)  # type: ignore[method-assign]

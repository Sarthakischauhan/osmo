"""Compatibility with Grok response.done status details sent as a string.

symphony-core 0.0.4 expects an object, but Grok can send "unimplemented".
Normalize the field before handing the event to the SDK so completion and tool calls
are not lost. Remove this adapter when the SDK accepts both representations.
"""

import json
from types import MethodType
from typing import Any

from core_ai.voice.realtime_api import RealtimeApiSession


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


def install_grok_compat(session: RealtimeApiSession) -> None:
    original = session._handle

    def handle(_session: Any, frame: str) -> None:
        original(normalize_response_done(frame))

    session._handle = MethodType(handle, session)  # type: ignore[method-assign]

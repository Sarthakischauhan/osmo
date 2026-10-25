"""Opt-in JSONL capture of the xAI realtime traffic, for reproducing model misbehaviour.

Enabled by OSMO_DEBUG_UPSTREAM=1. Only message bodies are written, never connection
headers; audio becomes its byte length and secret-looking fields are scrubbed.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from pathlib import Path
from typing import Any, Literal

SECRET_FIELD = re.compile(r"key|authorization|token|secret", re.IGNORECASE)
AUDIO_FIELDS = frozenset({"delta", "audio"})
REDACTED = "[redacted]"


def decoded_length(value: str) -> int:
    try:
        return len(base64.b64decode(value, validate=True))
    except (binascii.Error, ValueError):
        return len(value)


def redact(value: Any, audio_event: bool) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for name, field in value.items():
            if SECRET_FIELD.search(name):
                clean[name] = REDACTED
            elif isinstance(field, str) and (name == "audio" or (audio_event and name in AUDIO_FIELDS)):
                clean[name] = {"audio_bytes": decoded_length(field)}
            else:
                clean[name] = redact(field, audio_event)
        return clean
    if isinstance(value, list):
        return [redact(item, audio_event) for item in value]
    return value


class UpstreamLog:
    """Appends one line per upstream message: ts, direction, type and the redacted payload."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, direction: Literal["in", "out"], message: str | bytes) -> None:
        if isinstance(message, bytes):
            line: dict[str, Any] = {"type": "binary_audio", "payload": {"audio_bytes": len(message)}}
        else:
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError:
                parsed = {"unparseable_chars": len(message)}
            event_type = str(parsed.get("type", "")) if isinstance(parsed, dict) else ""
            line = {"type": event_type, "payload": redact(parsed, "audio" in event_type)}
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"ts": time.time(), "direction": direction, **line}) + "\n")

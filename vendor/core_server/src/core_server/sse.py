"""Server-sent event encoding for harness control-plane events."""

from __future__ import annotations

import json
from typing import Optional

from core_harness import ControlPlaneEvent


def encode_sse(event: ControlPlaneEvent, *, event_id: Optional[str] = None) -> str:
    data = json.dumps(event.model_dump(), ensure_ascii=False)
    run_id = event.payload.get("run_id")
    sequence = event.payload.get("seq")
    resolved_event_id = event_id or (
        f"{run_id}:{sequence}"
        if run_id is not None and sequence is not None
        else None
    )
    id_line = f"id: {resolved_event_id}\n" if resolved_event_id else ""
    return f"{id_line}event: {event.event_type}\ndata: {data}\n\n"


__all__ = ["encode_sse"]

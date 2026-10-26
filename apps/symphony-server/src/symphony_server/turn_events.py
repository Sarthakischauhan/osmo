"""How a harness run's events and a widget tap read on the voice side: typed run event
payloads, the user turn a tap stands for, and the activity label shown while a tool runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from symphony_server.protocol import WidgetAction
from symphony_server.widgets import OptionsWidget, Widget


class TextDeltaPayload(BaseModel):
    delta: str


class ToolStartedPayload(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = {}


class RunFailedPayload(BaseModel):
    message: str = "run failed"


def action_text(action: WidgetAction, widget: Widget) -> str:
    """A widget tap, phrased as the user turn it stands in for."""
    picked = action.value or ""
    if isinstance(widget, OptionsWidget):
        for index, choice in enumerate(widget.props.choices):
            if choice.id == picked:
                picked = f"option {index + 1}, {choice.label}"
    detail = f" -> {picked}" if picked else ""
    return (
        f"[Tapped on screen] {widget.type} widget {action.id}: {action.action}{detail}"
    )


def activity_label(name: str, args: Mapping[str, Any]) -> str:
    if name == "get_weather":
        return f"Checking weather · {args.get('location', '')}"
    if name == "get_stock_history":
        return f"Fetching prices · {str(args.get('symbol', '')).upper()}"
    if name == "web_search":
        return f"Searching the web · {args.get('query', '')}"
    if name == "create_image":
        return "Drawing a picture"
    return f"Running {name}"

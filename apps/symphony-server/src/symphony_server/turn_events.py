"""How a harness run's events and a widget tap read on the voice side: typed run event
payloads, the user turn a tap stands for, and the activity label shown while a tool runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from symphony_server.protocol import WidgetAction
from symphony_server.widgets import HtmlWidget, OptionsWidget, Widget


class TextDeltaPayload(BaseModel):
    delta: str


class ToolStartedPayload(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = {}


class RunFailedPayload(BaseModel):
    message: str = "run failed"


def action_text(action: WidgetAction, widget: Widget) -> str | None:
    """A widget tap, phrased as the user turn it stands in for; None for an html action the widget
    did not declare (the frame's script is model-written, so only declared ids count)."""
    if isinstance(widget, HtmlWidget):
        declared = next((button for button in widget.props.actions or [] if button.id == action.action), None)
        if declared is None:
            return None
        detail = f" -> {action.value}" if action.value else ""
        return f"[Tapped on screen] html widget {action.id}: {declared.label}{detail}"
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
    if name == "computer":
        return {"observe": "Looking at your screen", "click": "Clicking on your screen", "type": "Typing", "key": "Pressing keys", "scroll": "Scrolling"}.get(str(args.get("action")), "Using computer support")
    if name == "get_weather":
        return f"Checking weather · {args.get('location', '')}"
    if name == "get_stock_history":
        return f"Fetching prices · {str(args.get('symbol', '')).upper()}"
    if name == "web_search":
        return f"Searching the web · {args.get('query', '')}"
    if name == "browser_open":
        return f"Opening {args.get('url', '')}"
    if name == "browser_read":
        return "Reading the page"
    if name == "browser_click":
        return f"Clicking · {args.get('target', '')}"
    if name == "browser_type":
        return f"Typing · {args.get('target', '')}"
    if name == "browser_scroll":
        return f"Scrolling {args.get('direction', '')}"
    if name == "browser_back":
        return "Going back"
    if name == "browser_forward":
        return "Going forward"
    if name == "browser_press":
        return f"Pressing {args.get('key', '')} · {args.get('target', '')}"
    if name == "browser_select":
        return f"Choosing {args.get('option', '')}"
    if name == "browser_wait":
        return f"Waiting for “{args.get('text', '')}”"
    if name == "vault_search":
        return f"Searching your notes · {args.get('query', '')}"
    if name == "vault_read":
        return f"Reading {args.get('path', '')}"
    if name == "vault_write":
        verb = {"create": "Creating", "replace": "Rewriting", "append": "Adding to"}.get(str(args.get("mode") or "append"), "Writing")
        return f"{verb} {args.get('path', '')}"
    if name == "create_image":
        return "Drawing a picture"
    if name == "workspace_open":
        return f"Opening {args.get('repo', 'the repository')}"
    if name == "shell":
        command = " ".join(str(args.get("command", "")).split())
        return f"Running · {command[:80]}{'…' if len(command) > 80 else ''}"
    if name == "read_file":
        return f"Reading {args.get('path', '')}"
    if name in ("write_file", "edit_file"):
        return f"Editing {args.get('path', '')}"
    if name == "open_pull_request":
        return f"Opening a pull request · {args.get('title', '')}"
    if name == "write_code":
        lang = str(args.get("language") or "").strip()
        return f"Writing {lang} code" if lang else "Writing code"
    return f"Running {name}"

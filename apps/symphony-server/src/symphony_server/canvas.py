"""The canvas tools: render_widget, update_widget and create_image.

They are core_harness Tools run inside harness runs. Each validated widget is
kept on the process-wide `WidgetBoard` (so a later tap or update can find it)
and announced as an `osmo.widget` sink event whose payload is the Osmo wire
event the voice bridge forwards to the browser.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections import OrderedDict
from typing import Any

from core_ai import ModelRegistry
from core_harness import EventSink, Tool
from pydantic import ValidationError

from symphony_server.protocol import RenderedWidget, WidgetRender, WidgetUpdate
from symphony_server.widgets import (
    RENDER_WIDGET_PARAMETERS,
    UPDATE_WIDGET_PARAMETERS,
    CardProps,
    CardWidget,
    Widget,
    describe_widget,
    widget_adapter,
    widget_props,
)

# Sink event carrying a `widget.render` or `widget.update` wire event.
OSMO_WIDGET = "osmo.widget"
# Widgets remembered for taps and updates (LRU across all sessions).
WIDGETS_MAX = 512
IMAGE_PROMPT_MAX = 2000


class WidgetBoard:
    """Every widget shown recently, by id. Ids are unique across sessions: the browser keeps
    earlier widgets on screen when a new session starts."""

    def __init__(self, max_widgets: int = WIDGETS_MAX) -> None:
        self.max_widgets = max_widgets
        self.widgets: OrderedDict[str, Widget] = OrderedDict()

    def get(self, widget_id: str) -> Widget | None:
        return self.widgets.get(widget_id)

    def add(self, widget: Widget) -> str:
        # A fresh uuid per render: unique within and across sessions, so an update can only
        # reach its own widget.
        widget_id = f"w-{uuid.uuid4().hex}"
        self.put(widget_id, widget)
        return widget_id

    def put(self, widget_id: str, widget: Widget) -> None:
        self.widgets[widget_id] = widget
        self.widgets.move_to_end(widget_id)
        while len(self.widgets) > self.max_widgets:
            self.widgets.popitem(last=False)


async def show(board: WidgetBoard, sink: EventSink, widget: Widget) -> str:
    """Put `widget` on the board and the canvas; returns the tool result for the model."""
    widget_id = board.add(widget)
    event = WidgetRender(widget=RenderedWidget(id=widget_id, type=widget.type, props=widget_props(widget)))
    await sink.emit(OSMO_WIDGET, event.model_dump(mode="json"))
    return json.dumps({"shown": describe_widget(widget), "id": widget_id})


def compact_errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()[:5])


class RenderWidgetTool(Tool):
    def __init__(self, board: WidgetBoard) -> None:
        super().__init__(
            name="render_widget",
            description="Show a widget on the user's canvas. Returns what is now shown.",
            parameters=RENDER_WIDGET_PARAMETERS,
            category="osmo",
        )
        self.board = board

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        try:
            widget = widget_adapter.validate_python({"type": args.get("type"), "props": args.get("props")})
        except ValidationError as exc:
            return json.dumps({"shown": None, "error": f"invalid widget, nothing shown: {compact_errors(exc)}"})
        return await show(self.board, sink, widget)


class UpdateWidgetTool(Tool):
    def __init__(self, board: WidgetBoard) -> None:
        super().__init__(
            name="update_widget",
            description="Change props of a widget already on the canvas.",
            parameters=UPDATE_WIDGET_PARAMETERS,
            category="osmo",
        )
        self.board = board

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        widget_id = str(args.get("id", ""))
        patch = args.get("patch")
        current = self.board.get(widget_id)
        if current is None or not isinstance(patch, dict):
            return json.dumps({"error": f"no widget {widget_id!r} on screen or patch is not an object"})
        try:
            widget = widget_adapter.validate_python({"type": current.type, "props": {**widget_props(current), **patch}})
        except ValidationError as exc:
            return json.dumps({"error": f"invalid patch, widget unchanged: {compact_errors(exc)}"})
        self.board.put(widget_id, widget)
        props = widget_props(widget)
        event = WidgetUpdate(id=widget_id, patch={key: props.get(key) for key in patch})
        await sink.emit(OSMO_WIDGET, event.model_dump(mode="json"))
        return json.dumps({"shown": f"updated {describe_widget(widget)}", "id": widget_id})


# Media types a card can show inline (see widgets.DataImageUrl).
SHOWABLE_MEDIA = frozenset({"image/png", "image/jpeg", "image/webp"})


class CreateImageTool(Tool):
    """Draws a picture with core_ai's image generation and shows it as a card."""

    def __init__(self, registry: ModelRegistry, board: WidgetBoard, model_id: str) -> None:
        super().__init__(
            name="create_image",
            description="Draw a picture from a description and show it on the canvas as a card.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "What to draw, in detail."},
                    "title": {"type": "string", "description": "Short caption for the card."},
                },
                "required": ["prompt"],
            },
            category="image",
        )
        self.registry, self.board, self.model_id = registry, board, model_id

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        prompt = str(args.get("prompt", "")).strip()[:IMAGE_PROMPT_MAX]
        if not prompt:
            return json.dumps({"shown": None, "error": "prompt must describe the picture"})
        image, media_type = await self.registry.generate_image(prompt, model_id=self.model_id)
        if media_type not in SHOWABLE_MEDIA:
            return json.dumps({"shown": None, "error": f"the image model returned {media_type}, which cannot be shown"})
        title = str(args.get("title") or prompt)[:120]
        source = f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"
        return await show(self.board, sink, CardWidget(type="card", props=CardProps(title=title, body="", image=source)))

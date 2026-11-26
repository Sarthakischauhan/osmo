"""Widget validation and descriptions, and render_widget / update_widget: the background agent's widgets, kept on a
board by id."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from core_harness import EventSink
from pydantic import ValidationError

from symphony_server.canvas import OSMO_WIDGET, RenderWidgetTool, UpdateWidgetTool, WidgetBoard
from symphony_server.protocol import WidgetAction
from symphony_server.turn_events import action_text
from symphony_server.widgets import RENDER_WIDGET_PARAMETERS, describe_widget, widget_adapter


def test_html_widget_tool_schema_teaches_the_kit_and_the_bridge() -> None:
    description = RENDER_WIDGET_PARAMETERS["properties"]["props"]["description"]
    for token in ("Osmo UI kit", "card, stat", "button (primary)", "data-osmo-action", "osmo.action", "actions?: [{id,label}]", "self-contained"):
        assert token in description


def test_chart_description_names_series_and_points() -> None:
    widget = widget_adapter.validate_python(
        {"type": "chart", "props": {"kind": "line", "series": [{"name": "TSLA", "points": [{"x": "10-01", "y": 240}, {"x": "10-02", "y": 251}]}]}}
    )
    assert describe_widget(widget) == "line chart, TSLA, 2 points"


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "card", "props": {"title": "t", "body": "b", "onclick": "x"}},
        {"type": "options", "props": {"prompt": "p", "choices": []}},
        {"type": "chart", "props": {"kind": "line", "series": [{"name": "s", "points": [{"x": "a", "y": float("inf")}]}]}},
        {"type": "html", "props": {"html": ""}},
        {"type": "html", "props": {"html": "<p>x</p>", "title": ""}},
        {"type": "html", "props": {"html": "<p>x</p>", "title": "t" * 81}},
        {"type": "html", "props": {"html": "<p>x</p>", "actions": [{"id": f"a{i}", "label": "x"} for i in range(9)]}},
        {"type": "html", "props": {"html": "<p>x</p>", "actions": [{"id": "", "label": "x"}]}},
        {"type": "iframe", "props": {"src": "https://example.com"}},
    ],
)
def test_bad_widgets_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        widget_adapter.validate_python(payload)


def test_html_widget_title_is_optional_and_described() -> None:
    titled = widget_adapter.validate_python({"type": "html", "props": {"html": "<p>x</p>", "title": "Tip calculator"}})
    assert describe_widget(titled) == 'interactive html widget "Tip calculator"'
    untitled = widget_adapter.validate_python({"type": "html", "props": {"html": "<p>x</p>"}})
    assert describe_widget(untitled) == "interactive html widget"


def test_html_actions_are_described_and_only_declared_ids_become_a_user_turn() -> None:
    widget = widget_adapter.validate_python(
        {"type": "html", "props": {"html": "<p>x</p>", "title": "Review", "actions": [{"id": "approve", "label": "Approve"}]}}
    )
    assert describe_widget(widget) == 'interactive html widget "Review" with 1 actions'
    tap = WidgetAction(type="widget.action", id="w1", action="approve", value='{"note":"ship it"}')
    assert action_text(tap, widget) == '[Tapped on screen] html widget w1: Approve -> {"note":"ship it"}'
    assert action_text(WidgetAction(type="widget.action", id="w1", action="approve"), widget) == "[Tapped on screen] html widget w1: Approve"
    assert action_text(WidgetAction(type="widget.action", id="w1", action="delete"), widget) is None


WEATHER_CARD = {
    "type": "card",
    "props": {"title": "Palo Alto weekend", "body": "**72°** Sat · sunny\n\n**68°** Sun · partly cloudy"},
}


class Shown(EventSink):
    """The widget events a tool emitted."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    async def deliver(self, event_type: str, payload: dict[str, Any]) -> None:
        assert event_type == OSMO_WIDGET
        self.events.append(payload)


async def render(board: WidgetBoard, sink: Shown, widget: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(await RenderWidgetTool(board).execute(sink=sink, args=widget))
    return result


async def update(board: WidgetBoard, sink: Shown, widget_id: str, patch: Any) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(await UpdateWidgetTool(board).execute(sink=sink, args={"id": widget_id, "patch": patch}))
    return result


async def test_a_rendered_widget_gets_a_fresh_id_and_is_shown() -> None:
    board, sink = WidgetBoard(), Shown()
    result = await render(board, sink, WEATHER_CARD)
    assert re.fullmatch(r"w-[0-9a-f]{32}", result["id"])
    assert result == {"shown": 'card "Palo Alto weekend"', "id": result["id"]}
    assert sink.events == [{"type": "widget.render", "widget": {"id": result["id"], "type": "card", "props": WEATHER_CARD["props"]}}]
    # Ids never repeat, so an update can only reach its own widget (the browser keeps old widgets on screen).
    assert (await render(board, sink, WEATHER_CARD))["id"] != result["id"]


async def test_an_invalid_widget_is_never_shown() -> None:
    sink = Shown()
    result = await render(WidgetBoard(), sink, {"type": "chart", "props": {"kind": "pie", "series": []}})
    assert result["shown"] is None and "invalid widget" in result["error"]
    assert sink.events == []


async def test_an_update_touches_only_the_targeted_widget() -> None:
    board, sink = WidgetBoard(), Shown()
    first = (await render(board, sink, WEATHER_CARD))["id"]
    second = (await render(board, sink, {"type": "card", "props": {"title": "TSLA", "body": "Loading"}}))["id"]
    assert await update(board, sink, second, {"title": "TSLA $251"}) == {"shown": 'updated card "TSLA $251"', "id": second}
    assert sink.events[-1] == {"type": "widget.update", "id": second, "patch": {"title": "TSLA $251"}}
    # An empty patch reports the stored widget as is: the first card kept its own title.
    assert await update(board, sink, first, {}) == {"shown": f'updated card "{WEATHER_CARD["props"]["title"]}"', "id": first}


async def test_an_update_for_an_unknown_id_is_rejected() -> None:
    sink = Shown()
    assert "no widget 'w1'" in (await update(WidgetBoard(), sink, "w1", {"title": "x"}))["error"]
    assert sink.events == []

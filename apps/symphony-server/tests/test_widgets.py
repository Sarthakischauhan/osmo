from __future__ import annotations

import pytest
from pydantic import ValidationError

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

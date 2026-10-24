from __future__ import annotations

import pytest
from pydantic import ValidationError

from symphony_server.widgets import describe_widget, widget_adapter


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
        {"type": "iframe", "props": {"src": "https://example.com"}},
    ],
)
def test_bad_widgets_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        widget_adapter.validate_python(payload)

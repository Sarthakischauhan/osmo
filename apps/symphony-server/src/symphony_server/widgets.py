"""Widget prop schemas, mirroring packages/chat-sdk-widgets/src/schema.ts.

Every render_widget / update_widget call is validated here before anything is
sent to the browser, so a bad tool call never breaks the canvas.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WidgetButton(_Strict):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)


class CardProps(_Strict):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(max_length=4000)
    image: HttpUrl | None = None
    actions: list[WidgetButton] | None = Field(default=None, max_length=4)


class Choice(_Strict):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    detail: str | None = Field(default=None, max_length=60)


class OptionsProps(_Strict):
    prompt: str = Field(min_length=1, max_length=200)
    choices: list[Choice] = Field(min_length=1, max_length=8)


class Point(_Strict):
    x: str = Field(max_length=40)
    y: float

    @field_validator("y")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("y must be finite")
        return value


class Series(_Strict):
    name: str = Field(min_length=1, max_length=60)
    points: list[Point] = Field(min_length=1, max_length=400)


class ChartProps(_Strict):
    kind: Literal["line", "bar"]
    series: list[Series] = Field(min_length=1, max_length=5)


class HtmlProps(_Strict):
    html: str = Field(min_length=1, max_length=100_000)


class CardWidget(_Strict):
    type: Literal["card"]
    props: CardProps


class OptionsWidget(_Strict):
    type: Literal["options"]
    props: OptionsProps


class ChartWidget(_Strict):
    type: Literal["chart"]
    props: ChartProps


class HtmlWidget(_Strict):
    type: Literal["html"]
    props: HtmlProps


Widget = Annotated[
    Union[CardWidget, OptionsWidget, ChartWidget, HtmlWidget],
    Field(discriminator="type"),
]
widget_adapter: TypeAdapter[Widget] = TypeAdapter(Widget)


def widget_props(widget: Widget) -> dict[str, Any]:
    return widget.props.model_dump(mode="json", exclude_none=True)


def describe_widget(widget: Widget) -> str:
    """One line telling the model what is now on screen (the `shown` result)."""
    if isinstance(widget, CardWidget):
        buttons = widget.props.actions
        return f'card "{widget.props.title}"' + (f" with {len(buttons)} buttons" if buttons else "")
    if isinstance(widget, OptionsWidget):
        listed = "; ".join(f"{i + 1}. {c.label}" for i, c in enumerate(widget.props.choices))
        return f'options "{widget.props.prompt}", {len(widget.props.choices)} choices: {listed}'
    if isinstance(widget, ChartWidget):
        names = ", ".join(s.name for s in widget.props.series)
        return f"{widget.props.kind} chart, {names}, {len(widget.props.series[0].points)} points"
    return "interactive html widget"


RENDER_WIDGET_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["card", "options", "chart", "html"]},
        "props": {
            "type": "object",
            "description": (
                "card: {title, body (markdown), image?, actions?: [{id,label}]}; "
                "options: {prompt, choices: [{id,label,detail?}]}; "
                "chart: {kind: 'line'|'bar', series: [{name, points: [{x, y}]}]}; "
                "html: {html} (a complete self-contained document; it runs in a sandboxed iframe)"
            ),
        },
    },
    "required": ["type", "props"],
}

UPDATE_WIDGET_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "id returned by render_widget"},
        "patch": {"type": "object", "description": "props to replace on that widget"},
    },
    "required": ["id", "patch"],
}

"""Widget prop schemas, mirroring packages/osmo-widgets/src/schema.ts.

Every render_widget / update_widget call is validated here before anything is
sent to the browser, so a bad tool call never breaks the canvas.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, TypeAdapter, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WidgetButton(_Strict):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)


# A picture made by create_image travels inline as base64; any other image must be a web URL.
DataImageUrl = Annotated[str, StringConstraints(pattern=r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$")]


class CardProps(_Strict):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(max_length=4000)
    image: HttpUrl | DataImageUrl | None = None
    art: Literal["sun", "moon", "cloud", "rain", "snow", "storm", "wind", "stars"] | None = None
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
    title: str | None = Field(default=None, min_length=1, max_length=80)
    # The ids the frame may send back (osmo.action / data-osmo-action); a tap on any other id is dropped.
    actions: list[WidgetButton] | None = Field(default=None, max_length=8)


class BrowserProps(_Strict):
    """The agent browser: `frame` is the latest screencast frame, `url` the page it shows."""

    status: Literal["live", "ended"]
    url: str | None = None
    frame: DataImageUrl | None = None


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


class BrowserWidget(_Strict):
    type: Literal["browser"]
    props: BrowserProps


Widget = Annotated[
    Union[CardWidget, OptionsWidget, ChartWidget, HtmlWidget, BrowserWidget],
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
    if isinstance(widget, BrowserWidget):
        return "agent browser"
    named = f'interactive html widget "{widget.props.title}"' if widget.props.title else "interactive html widget"
    return f"{named} with {len(widget.props.actions)} actions" if widget.props.actions else named


RENDER_WIDGET_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["card", "options", "chart", "html"]},
        "props": {
            "type": "object",
            "description": (
                "card: {title, body (markdown), image?, art?: sun|moon|cloud|rain|snow|storm|wind|stars, actions?: [{id,label}]}; "
                "options: {prompt, choices: [{id,label,detail?}]}; "
                "chart: {kind: 'line'|'bar', series: [{name, points: [{x, y}]}]}; "
                "html: {html, title?, actions?: [{id,label}]} (a self-contained document in a sandboxed iframe with no network, "
                "shown under a header bar with title, reload, zoom and fullscreen; inline CSS/JS only, no remote resources, "
                "images only as data: URLs). The Osmo UI kit is already loaded (Geist look, dark/light follows the app): "
                "write short semantic HTML with its classes instead of inline CSS: "
                "card, stat (with .label and .value children), grid, row, table, badge (green|amber|red|blue), kbd, "
                "button (primary), input, tabs (children with aria-selected), list, empty, progress (a span child with a width), muted, mono. "
                "Use green, amber and red only for semantic status, and keep controls labelled and keyboard reachable. "
                "To let the user answer, declare actions and mark elements with data-osmo-action=\"<id>\" "
                "(optional data-osmo-value), or call osmo.action(id, value) from script; a form with data-osmo-action "
                "sends its fields as JSON. Undeclared ids are ignored.)"
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

"""The parts of Chrome DevTools Protocol replies the agent browser reads, as pydantic models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class CdpMessage(BaseModel):
    """A CDP reply or event: the fields Osmo reads, ignoring the rest."""

    model_config = ConfigDict(extra="ignore")


class CreatedTarget(CdpMessage):
    """Target.createTarget's reply: the new page."""

    targetId: str


class AttachedTarget(CdpMessage):
    """Target.attachToTarget's reply: the CDP session on the page."""

    sessionId: str


class NavigateResult(CdpMessage):
    """Page.navigate's reply: an error when the page could not be reached."""

    errorText: str | None = None
    # Omitted by Chrome for a same-document navigation (only the #fragment changed): no load follows.
    loaderId: str | None = None


class FrameMetadata(CdpMessage):
    """What Chrome says about the page a frame shows; the size is the viewport in CSS pixels, the unit of input events."""

    deviceWidth: float | None = None
    deviceHeight: float | None = None


class ScreencastFrame(CdpMessage):
    """A Page.screencastFrame event: one frame as base64 image data, to acknowledge by its session id."""

    data: str
    sessionId: int
    metadata: FrameMetadata = FrameMetadata()


class NavigatedFrame(CdpMessage):
    """The frame of a Page.frameNavigated event (the main frame has no parent)."""

    id: str
    parentId: str | None = None
    url: str


class FrameNavigated(CdpMessage):
    """A Page.frameNavigated event."""

    frame: NavigatedFrame


class RemoteObject(CdpMessage):
    """A JavaScript value returned by value."""

    value: Any = None


class Evaluated(CdpMessage):
    """Runtime.evaluate's (or callFunctionOn's) reply: the value, or the exception it threw."""

    result: RemoteObject
    exceptionDetails: dict[str, Any] | None = None


class PageText(BaseModel):
    """What READ_EXPRESSION reads off a page: its address, title and visible text."""

    url: str
    title: str
    text: str


class AxValue(CdpMessage):
    """A value in the accessibility tree."""

    value: Any = None


class AxProperty(CdpMessage):
    """A named property of an accessibility node."""

    name: str
    value: AxValue


class AxNode(CdpMessage):
    """One node of the accessibility tree: its role, name and the DOM node behind it."""

    ignored: bool = False
    role: AxValue | None = None
    name: AxValue | None = None
    backendDOMNodeId: int | None = None
    properties: list[AxProperty] = []


class AxTree(CdpMessage):
    """Accessibility.getFullAXTree's reply."""

    nodes: list[AxNode]


class BoxModel(CdpMessage):
    """An element's content quad (four x, y corners in CSS pixels)."""

    content: list[float]


class BoxModelResult(CdpMessage):
    """DOM.getBoxModel's reply."""

    model: BoxModel


class DescribedNode(CdpMessage):
    """A DOM node's name and attributes."""

    nodeName: str = ""
    # Flat: name, value, name, value...
    attributes: list[str] = []


class Described(CdpMessage):
    """DOM.describeNode's reply."""

    node: DescribedNode = DescribedNode()


class ResolvedObject(CdpMessage):
    """The JavaScript object of a DOM node."""

    objectId: str


class Resolved(CdpMessage):
    """DOM.resolveNode's reply."""

    object: ResolvedObject


class HistoryEntry(CdpMessage):
    """One entry of the page's history."""

    id: int
    url: str


class NavigationHistory(CdpMessage):
    """Page.getNavigationHistory's reply: the entries and where the page is among them."""

    currentIndex: int
    entries: list[HistoryEntry]


class PausedRequest(CdpMessage):
    """The request of a Fetch.requestPaused event."""

    url: str


class RequestPaused(CdpMessage):
    """A Fetch.requestPaused event: a request (or a redirect hop of one) Chrome holds until it is continued or failed."""

    requestId: str
    request: PausedRequest
    resourceType: str = ""

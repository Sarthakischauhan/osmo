"""One launched Chrome with one page: the CDP calls the browser tools need, the user's own input and the screencast relay."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from symphony_server.browser_cdp_models import (
    AttachedTarget,
    AxTree,
    BoxModelResult,
    CreatedTarget,
    Described,
    Evaluated,
    FrameMetadata,
    FrameNavigated,
    NavigateResult,
    NavigationHistory,
    PageText,
    RequestPaused,
    Resolved,
    ScreencastFrame,
)
from symphony_server.browser_guard import NetworkGuard
from symphony_server.browser_page import ENTER_KEY_CODE, READ_EXPRESSION, SELECT_FUNCTION, TEXT_ROLES, frame_data_url, is_focusable, role_of, sensitive_kind
from symphony_server.browser_rules import DomainRules
from symphony_server.cdp import BrowserError, CdpConnection
from symphony_server.chrome import OWNER_FILE, drain, kill_group, listening_url, start_chrome
from symphony_server.host_names import normalize_host
from symphony_server.protocol import BrowserClick, BrowserKey, BrowserScroll, RenderedWidget, ServerEvent, WidgetRender, WidgetUpdate
from symphony_server.widgets import BrowserProps

log = logging.getLogger("symphony.browser")

# How long browser_back/forward wait for the page they go to, and the longest browser_wait.
HISTORY_WAIT_SECONDS = 10.0
WAIT_MAX_SECONDS = 10.0
WAIT_POLL_SECONDS = 0.25
# Every request Chrome makes, redirect hops included, is held until the guard has looked at it.
INTERCEPT_ALL = {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
Show = Callable[[str, ServerEvent], Awaitable[None]]


class BrowserSession:
    """One launched Chrome with one page: the CDP calls the tools need, and the screencast relay."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        profile: Path,
        cdp: CdpConnection,
        target: str,
        widget_id: str,
        show: Callable[[ServerEvent], Awaitable[None]],
        keep_profile: bool = False,
        rules: DomainRules = DomainRules(),
        guard: NetworkGuard | None = None,
    ) -> None:
        self.process, self.profile, self.cdp, self.target, self.widget_id, self.show = process, profile, cdp, target, widget_id, show
        # A persistent profile (Osmo's own) stays when the browser closes; a throwaway one is removed.
        self.keep_profile, self.rules = keep_profile, rules
        self.guard = guard if guard is not None else NetworkGuard()
        # Why the last page (a navigation or a redirect hop of one) was refused, for the error the tool returns.
        self.refused: str | None = None
        # The site an approval was decided for (BrowserApprovalPolicy), checked again just before the action.
        self.decided_host: str | None = None
        # Sites the user browsed while they had taken the browser over: their logins may be in the profile now, so
        # an `allow` rule no longer lets the agent act there without asking.
        self.user_hosts: set[str] = set()
        # The user took the browser over from the widget: agent tools refuse until they hand it back.
        self.taken_over = False
        self.drain: asyncio.Task[None] | None = None
        self.relays: set[asyncio.Task[None]] = set()
        self.closed = False
        # What the widget shows now (kept to put it back for a client that connects later), and the size
        # of the viewport in CSS pixels as of the last frame (to place the user's pointer on the page).
        self.props = BrowserProps(status="live")
        self.viewport: FrameMetadata | None = None
        # The user's clicks, scrolls and keys run one at a time, in the order they came.
        self.input_lock = asyncio.Lock()
        # When the browser was last used, on the clock of its `BrowserSessions` (set there).
        self.last_used = 0.0

    @classmethod
    async def launch(
        cls,
        executable: str,
        widget_id: str,
        show: Callable[[ServerEvent], Awaitable[None]],
        profile: Path | None = None,
        visible: bool = False,
        rules: DomainRules = DomainRules(),
        guard: NetworkGuard | None = None,
    ) -> BrowserSession:
        """Start Chrome (on `profile`, or a throwaway one) and open a blank page on it; kills it again if anything fails or is cancelled."""
        keep_profile = profile is not None
        process, profile = await start_chrome(executable, profile, visible)
        session: BrowserSession | None = None
        try:
            url = await listening_url(process)
            cdp = await CdpConnection.open(url)
            try:
                target = CreatedTarget.model_validate(await cdp.call("Target.createTarget", {"url": "about:blank"})).targetId
                attached = AttachedTarget.model_validate(await cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True}))
            except BaseException:
                await cdp.close()
                raise
            session = cls(process, profile, cdp, attached.sessionId, widget_id, show, keep_profile, rules, guard)
            session.drain = asyncio.create_task(drain(process))
            return session
        except BaseException:
            if session is not None:
                await session.close()
            else:
                kill_group(process)
                await process.wait()
                forget_profile(profile, keep_profile)
            raise

    @property
    def usable(self) -> bool:
        """Chrome still runs and its DevTools connection is up."""
        return not self.closed and self.process.returncode is None and not self.cdp.reader.done()

    def render(self) -> WidgetRender:
        """The widget as it is now: what a client that connects to the thread again is shown."""
        return WidgetRender(widget=RenderedWidget(id=self.widget_id, type="browser", props=self.props.model_dump(mode="json", exclude_none=True)))

    async def guard_network(self) -> None:
        """Hold every request of the whole browser (pages, frames, workers, redirect hops) for `screen`.

        Fetch is enabled on the browser target, not the page, so service workers and popups are covered too."""
        self.cdp.on("Fetch.requestPaused", self.on_request)
        await self.cdp.call("Fetch.enable", INTERCEPT_ALL)

    def on_request(self, params: dict[str, Any], session_id: str | None) -> None:
        """Screen one paused request beside the CDP reader."""
        self.spawn(self.screen(RequestPaused.model_validate(params), session_id))

    async def screen(self, paused: RequestPaused, session_id: str | None) -> None:
        """Let a request through, or fail it when it goes to this machine or its network, or (a page the agent
        opens) to a site the user's rules deny."""
        url = paused.request.url
        reason = await self.guard.refusal(url)
        if reason is None and paused.resourceType == "Document" and not self.taken_over and self.rules.decide(url) == "deny":
            reason = f"{urlsplit(url).hostname} is blocked by the user's site rules"
        if reason is None:
            await self.cdp.call("Fetch.continueRequest", {"requestId": paused.requestId}, session_id=session_id)
            return
        if paused.resourceType == "Document":
            self.refused = reason
        log.info("browser request refused: %s", reason)
        await self.cdp.call("Fetch.failRequest", {"requestId": paused.requestId, "errorReason": "BlockedByClient"}, session_id=session_id)

    def site_of(self, url: str | None) -> str | None:
        """The normalised host of `url` (None without one, as about:blank)."""
        if not url:
            return None
        try:
            host = normalize_host(urlsplit(url).hostname or "")
        except ValueError:
            return None
        return host or None

    async def start_screencast(self) -> None:
        """Start relaying the page's frames and address to the widget."""
        self.cdp.on("Page.screencastFrame", self.on_frame)
        self.cdp.on("Page.frameNavigated", self.on_navigated)
        await self.page("Page.enable")
        await self.page("Page.startScreencast")

    async def page(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a CDP method on the page's session."""
        return await self.cdp.call(method, params, session_id=self.target)

    # ------------------------------------------------------------ screencast

    def on_frame(self, params: dict[str, Any], session_id: str | None) -> None:
        """Relay one screencast frame to the widget (and acknowledge it, so Chrome sends the next)."""
        if session_id != self.target:
            return
        frame = ScreencastFrame.model_validate(params)
        if frame.metadata.deviceWidth is not None and frame.metadata.deviceHeight is not None:
            self.viewport = frame.metadata
        self.spawn(self.relay(frame))

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        """Run a relay beside the CDP reader, keeping it until it ends."""
        relay = asyncio.create_task(work)
        self.relays.add(relay)
        relay.add_done_callback(self.relayed)

    def relayed(self, relay: asyncio.Task[None]) -> None:
        """Forget a finished relay, logging its failure."""
        self.relays.discard(relay)
        if not relay.cancelled() and relay.exception() is not None:
            log.warning("browser frame relay failed: %s", relay.exception())

    async def patch(self, props: dict[str, Any]) -> None:
        """Change the widget's props and send the change to the thread."""
        self.props = self.props.model_copy(update=props)
        await self.show(WidgetUpdate(id=self.widget_id, patch=props))

    async def relay(self, frame: ScreencastFrame) -> None:
        """Show one frame, then ack it: Chrome sends the next frame only after the ack."""
        try:
            await self.patch({"frame": frame_data_url(frame.data)})
        finally:
            if not self.closed:
                await self.page("Page.screencastFrameAck", {"sessionId": frame.sessionId})

    def on_navigated(self, params: dict[str, Any], session_id: str | None) -> None:
        """Show the main frame's new address and the rule for its site on the widget."""
        navigated = FrameNavigated.model_validate(params).frame
        if session_id == self.target and navigated.parentId is None:
            host = self.site_of(navigated.url)
            if self.taken_over and host is not None:
                self.user_hosts.add(host)
            self.spawn(self.patch({"url": navigated.url, "rule": self.rules.decide(navigated.url)}))

    # ------------------------------------------------------------ what the tools do

    async def navigate(self, url: str) -> str:
        """Go to `url` and wait for it to load; BrowserError when it cannot be reached."""
        loaded = self.cdp.expect("Page.loadEventFired", self.target)
        self.refused = None
        try:
            result = NavigateResult.model_validate(await self.page("Page.navigate", {"url": url}))
            if result.errorText is not None:
                raise BrowserError(f"could not open {url}: {self.refused or result.errorText}")
            if result.loaderId is not None:
                await loaded
        finally:
            loaded.cancel()
        return (await self.read_page()).url

    async def read_page(self) -> PageText:
        """The page's address, title and visible text."""
        evaluated = Evaluated.model_validate(await self.page("Runtime.evaluate", {"expression": READ_EXPRESSION, "returnByValue": True}))
        if evaluated.exceptionDetails is not None:
            raise BrowserError(f"could not read the page: {evaluated.exceptionDetails.get('text', 'script error')}")
        return PageText.model_validate(evaluated.result.value)

    async def read(self) -> dict[str, Any]:
        """The page's text and controls. Nothing typed into a field is included (not even its AX value), and secret
        fields are marked `sensitive` so the model knows to leave them to the user."""
        page = await self.read_page()
        tree = AxTree.model_validate(await self.page("Accessibility.getFullAXTree"))
        elements: list[dict[str, Any]] = []
        for node in tree.nodes:
            if node.ignored or node.backendDOMNodeId is None or not is_focusable(node) or role_of(node) == "RootWebArea":
                continue
            element: dict[str, Any] = {"ref": node.backendDOMNodeId, "role": role_of(node), "name": node.name.value if node.name else ""}
            kind = await self.sensitive(node.backendDOMNodeId) if role_of(node) in TEXT_ROLES else None
            if kind is not None:
                element["sensitive"] = kind
            elements.append(element)
        return {"url": page.url, "title": page.title, "text": page.text, "elements": elements}

    async def sensitive(self, ref: int) -> str | None:
        """What secret the element `ref` holds, or None."""
        return sensitive_kind(Described.model_validate(await self.page("DOM.describeNode", {"backendNodeId": ref})).node)

    async def center_of(self, ref: int) -> tuple[float, float]:
        """The middle of the element `ref`, scrolled into view."""
        await self.page("DOM.scrollIntoViewIfNeeded", {"backendNodeId": ref})
        quad = BoxModelResult.model_validate(await self.page("DOM.getBoxModel", {"backendNodeId": ref})).model.content
        return sum(quad[0::2]) / 4, sum(quad[1::2]) / 4

    async def click(self, ref: int) -> None:
        """Click the middle of the element `ref`."""
        x, y = await self.center_of(ref)
        await self.tap(x, y)

    async def tap(self, x: float, y: float) -> None:
        """Press and release the left button at (x, y)."""
        for kind in ("mousePressed", "mouseReleased"):
            await self.page("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})

    async def type(self, ref: int, text: str, submit: bool) -> None:
        """Type `text` into the element `ref` (never a sensitive one), pressing Enter after when `submit`."""
        kind = await self.sensitive(ref)
        if kind is not None:
            raise BrowserError(
                f"that is a {kind} field: Osmo never types secrets. Ask the user to take over the browser and enter it themselves"
            )
        await self.page("DOM.focus", {"backendNodeId": ref})
        await self.page("Input.insertText", {"text": text})
        if submit:
            await self.press("Enter", "Enter", ENTER_KEY_CODE)

    async def press(self, key: str, code: str, key_code: int) -> None:
        """Press and release a key (`key` and `code` as a DOM KeyboardEvent has them, `key_code` its keyCode)."""
        text = "\r" if key == "Enter" else key if len(key) == 1 else None
        keys = {"key": key, "code": code, "windowsVirtualKeyCode": key_code}
        # A key that types something is a keyDown with its text; one that does not (Backspace, arrows) is a rawKeyDown.
        await self.page("Input.dispatchKeyEvent", {"type": "rawKeyDown" if text is None else "keyDown", **keys, **({} if text is None else {"text": text})})
        await self.page("Input.dispatchKeyEvent", {"type": "keyUp", **keys})

    async def go(self, delta: Literal[-1, 1]) -> str:
        """Back (-1) or forward (1) in the tab's history; the address it lands on."""
        history = NavigationHistory.model_validate(await self.page("Page.getNavigationHistory"))
        index = history.currentIndex + delta
        if not 0 <= index < len(history.entries):
            raise BrowserError("there is no page to go " + ("back to" if delta < 0 else "forward to"))
        loaded = self.cdp.expect("Page.loadEventFired", self.target)
        try:
            await self.page("Page.navigateToHistoryEntry", {"entryId": history.entries[index].id})
            # A page from the back/forward cache fires no load: give up waiting after a while, it is there anyway.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(HISTORY_WAIT_SECONDS):
                    await loaded
        finally:
            loaded.cancel()
        return (await self.read_page()).url

    async def select(self, ref: int, option: str) -> dict[str, Any]:
        """Choose an option of the dropdown `ref` by its value or label."""
        resolved = Resolved.model_validate(await self.page("DOM.resolveNode", {"backendNodeId": ref}))
        called = Evaluated.model_validate(
            await self.page(
                "Runtime.callFunctionOn",
                {"objectId": resolved.object.objectId, "functionDeclaration": SELECT_FUNCTION, "arguments": [{"value": option}], "returnByValue": True},
            )
        )
        result = called.result.value
        if called.exceptionDetails is not None or not isinstance(result, dict):
            raise BrowserError("could not choose that option")
        if "error" in result:
            raise BrowserError(json.dumps(result))
        return {"selected": str(result["selected"])}

    async def wait_for(self, text: str, seconds: float) -> bool:
        """Whether `text` shows up in the page's text within `seconds`."""
        expression = f"(document.body ? document.body.innerText : '').includes({json.dumps(text)})"
        deadline = time.monotonic() + seconds
        while True:
            found = Evaluated.model_validate(await self.page("Runtime.evaluate", {"expression": expression, "returnByValue": True}))
            if found.result.value is True:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(WAIT_POLL_SECONDS)

    async def scroll(self, direction: Literal["down", "up"]) -> None:
        """Scroll the page one screen down or up."""
        sign = "" if direction == "down" else "-"
        await self.page("Runtime.evaluate", {"expression": f"window.scrollBy(0, {sign}window.innerHeight)"})

    # ------------------------------------------------------------ what the user does in the widget

    def viewport_point(self, x: float, y: float) -> tuple[float, float]:
        """A point of the displayed frame (0 to 1 across and down, as the widget measured it) in viewport CSS pixels."""
        if self.viewport is None or self.viewport.deviceWidth is None or self.viewport.deviceHeight is None:
            raise BrowserError("the browser has shown no frame yet, so a point on it is not known")
        return x * self.viewport.deviceWidth, y * self.viewport.deviceHeight

    async def drive(self, event: BrowserClick | BrowserScroll | BrowserKey) -> None:
        """Do what the user did in the widget, straight through CDP: the user's own input needs no approval."""
        async with self.input_lock:
            if isinstance(event, BrowserKey):
                await self.press(event.key, event.code, event.keyCode)
                return
            x, y = self.viewport_point(event.x, event.y)
            if isinstance(event, BrowserClick):
                await self.tap(x, y)
            else:
                await self.page("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": event.deltaX, "deltaY": event.deltaY})

    # ------------------------------------------------------------ lifetime

    async def close(self) -> None:
        """Stop relaying, kill Chrome and remove its profile. Safe to call twice."""
        if self.closed:
            return
        self.closed = True
        relays = tuple(self.relays)
        for relay in relays:
            relay.cancel()
        await asyncio.gather(*relays, return_exceptions=True)
        if self.drain is not None:
            self.drain.cancel()
        await self.cdp.close()
        kill_group(self.process)
        await self.process.wait()
        forget_profile(self.profile, self.keep_profile)


def forget_profile(profile: Path, keep: bool) -> None:
    """After Chrome is gone: remove a throwaway profile; a kept one only loses its owner file (no server runs it now)."""
    if keep:
        (profile / OWNER_FILE).unlink(missing_ok=True)
    else:
        shutil.rmtree(profile, ignore_errors=True)

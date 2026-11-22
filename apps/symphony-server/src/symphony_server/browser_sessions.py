"""The agent browser: one CDP browser session per Osmo thread, driven by Osmo's browser tools and by the user.

`BrowserSessions` owns the sessions, keyed by thread id (the agent runs of a thread pass it to the tools through
`BrowserRunLink`). A thread's first browser tool call launches a headless Chrome on a throwaway profile, shows an
`agent browser` widget on the thread's canvas and starts a CDP screencast: every frame Chrome sends becomes a
`widget.update` for that widget. The session outlives the run that opened it: later runs of the thread reuse
it and the widget keeps streaming between runs. It closes only on an explicit close (`BrowserSessions.close`, from
the widget's close button), when the thread's voice session ends, when it has sat idle for `IDLE_CLOSE_SECONDS`,
or when the server shuts down; closing kills Chrome, removes the profile and leaves the widget frozen on its last
frame. A thread's live agent runs hold its browser: the end of the voice session waits for them to finish, and
an idle browser is not closed while one runs. A client that connects to the thread
again gets the widget back (`BrowserSession.render`).

The user can click, scroll and type into the page from the widget's fullscreen view (`BrowserSession.drive`); that
is the user's own input and needs no approval, unlike the agent's `browser_click` and `browser_type`. The widget's
Take over button hands the browser to the user (`BrowserSessions.take_over`): until they hand it back, every agent
browser tool refuses, so the user can log in or pay without the agent acting under them.

What the model sees of a page is its text and its controls; password, one-time-code and card fields are marked
`sensitive` and the agent cannot type into them (`sensitive_kind`), so secrets stay the user's to enter.
Per-domain rules (`browser_rules.DomainRules`) deny sites outright here and, through `BrowserApprovalPolicy`,
let the approval addon skip or force approval for clicks, typing and key presses by the site of the page.

Chrome starts headless on a throwaway profile by default; `visible` shows its window too, and `profile` (Osmo's
own persistent profile, opt-in) keeps logins between sessions for one thread at a time (others get a throwaway one).

Frames and the widget's render reach the browser straight through `show` (the thread's observer),
not as run events: core_server keeps every run event in memory for replay.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from core_harness import Addon

from symphony_server.browser_rules import DomainRules
from symphony_server.browser_session import BrowserSession, Show
from symphony_server.canvas import WidgetBoard, render_event
from symphony_server.cdp import BrowserError
from symphony_server.chrome import BrowserUnavailable
from symphony_server.protocol import BrowserClick, BrowserKey, BrowserScroll, ServerEvent, WidgetUpdate
from symphony_server.widgets import BrowserProps, BrowserWidget

log = logging.getLogger("symphony.browser")

# The key of RunContext.metadata that carries the thread an agent run belongs to (the key of its browser).
THREAD_ID = "thread_id"
# A browser nobody has used (no tool call, no user input, no thread opened) for this long is closed.
IDLE_CLOSE_SECONDS = 30 * 60
# How often `watch_idle` looks for idle browsers.
IDLE_CHECK_SECONDS = 60


class BrowserSessions:
    """The browser session of each thread, by thread id, and what to do with it.

    `show` is wired by `AgentRuns` to the observer of the thread (a client watching it).
    A session lives until `close` (the widget's close button), `end` (the end of the thread's voice session, once
    its runs are done), `reap_idle` or `close_all` (server shutdown); the end of an agent run does not close it.
    `clock` tells the time for the idle check (seconds, monotonic).
    """

    def __init__(
        self,
        executable: str | None,
        board: WidgetBoard,
        clock: Callable[[], float] = time.monotonic,
        *,
        profile: Path | None = None,
        visible: bool = False,
        rules: DomainRules = DomainRules(),
    ) -> None:
        self.executable, self.board, self.clock = executable, board, clock
        self.profile, self.visible, self.rules = profile, visible, rules
        self.sessions: dict[str, BrowserSession] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        # The agent runs of each thread that are not over, which keep its browser open.
        self.holds: dict[str, int] = {}
        # Threads whose voice session ended while a run held the browser: it closes with the last run.
        self.ending: set[str] = set()
        self.show: Show = discard

    async def open(self, thread_id: str) -> BrowserSession:
        """The thread's browser: the one that is running, or a new one (its widget shown) on first use or after Chrome died."""
        if self.executable is None:
            raise BrowserUnavailable("no Chrome or Chromium was found: install one, or set OSMO_BROWSER_PATH to its executable")
        async with self.locks.setdefault(thread_id, asyncio.Lock()):
            session = self.sessions.get(thread_id)
            if session is not None and not session.usable:
                await self.close(thread_id)
                session = None
            if session is None:
                session = await self.start(thread_id, self.executable)
                self.sessions[thread_id] = session
            session.last_used = self.clock()
            return session

    async def start(self, thread_id: str, executable: str) -> BrowserSession:
        """Launch Chrome for `thread_id`, show its widget and start the screencast."""
        render = render_event(self.board, BrowserWidget(type="browser", props=BrowserProps(status="live")))

        async def show(event: ServerEvent) -> None:
            """Send the session's events to whoever watches the thread now."""
            await self.show(thread_id, event)

        # Osmo's persistent profile serves one browser at a time (Chrome locks it); the others get a throwaway one.
        in_use = any(session.keep_profile for session in self.sessions.values())
        profile = self.profile if self.profile is not None and not in_use else None
        session = await BrowserSession.launch(executable, render.widget.id, show, profile, self.visible, self.rules)
        try:
            await show(render)
            await session.start_screencast()
        except BaseException:
            # Cancelled (or failed) after the widget was shown: close chrome and freeze the widget.
            await session.close()
            await show(WidgetUpdate(id=render.widget.id, patch={"status": "ended"}))
            raise
        return session

    def touch(self, thread_id: str) -> None:
        """Note that someone used the thread's browser (if it has one) just now."""
        session = self.sessions.get(thread_id)
        if session is not None:
            session.last_used = self.clock()

    def widget_session(self, thread_id: str, widget_id: str) -> BrowserSession | None:
        """The browser session of `thread_id`, when `widget_id` is its widget (a client can only drive its own thread's)."""
        session = self.sessions.get(thread_id)
        return session if session is not None and session.widget_id == widget_id else None

    async def drive(self, thread_id: str, event: BrowserClick | BrowserScroll | BrowserKey) -> None:
        """Pass the user's click, scroll or key on the thread's browser widget to the page (no approval: it is the user's own)."""
        session = self.widget_session(thread_id, event.id)
        if session is None:
            return
        self.touch(thread_id)
        try:
            await session.drive(event)
        except BrowserError as exc:
            log.warning("browser input failed: %s", exc)

    def hold(self, thread_id: str) -> None:
        """An agent run of the thread started: its browser stays open until the run is over."""
        self.holds[thread_id] = self.holds.get(thread_id, 0) + 1

    async def release(self, thread_id: str) -> None:
        """An agent run of the thread is over. The last one closes a browser whose voice session has ended."""
        left = self.holds.get(thread_id, 0) - 1
        if left > 0:
            self.holds[thread_id] = left
            return
        self.holds.pop(thread_id, None)
        if thread_id in self.ending:
            self.ending.discard(thread_id)
            await self.close(thread_id)
        else:
            self.touch(thread_id)

    async def end(self, thread_id: str) -> None:
        """The thread's voice session ended: close its browser, or leave that to the last run that holds it."""
        if self.holds.get(thread_id, 0) > 0:
            self.ending.add(thread_id)
        else:
            await self.close(thread_id)

    def resume(self, thread_id: str) -> None:
        """A voice session began on the thread: its browser is not on its way out any more."""
        self.ending.discard(thread_id)
        self.touch(thread_id)

    async def take_over(self, thread_id: str, user: bool) -> None:
        """The user takes the thread's browser over (`user`) or hands it back to the agent."""
        session = self.sessions.get(thread_id)
        if session is not None and session.usable:
            session.taken_over = user
            session.last_used = self.clock()
            await session.patch({"control": "user" if user else "agent"})

    async def close(self, thread_id: str) -> None:
        """End a thread's browser: kill it and freeze its widget on the last frame."""
        session = self.sessions.pop(thread_id, None)
        if session is not None:
            await session.close()
            await self.show(thread_id, WidgetUpdate(id=session.widget_id, patch={"status": "ended"}))

    async def reap_idle(self) -> None:
        """Close the browsers that have been idle for `IDLE_CLOSE_SECONDS` and that no run holds."""
        for thread_id in tuple(self.sessions):
            async with self.locks.setdefault(thread_id, asyncio.Lock()):
                session = self.sessions.get(thread_id)
                if session is not None and not self.holds.get(thread_id) and self.clock() - session.last_used >= IDLE_CLOSE_SECONDS:
                    await self.close(thread_id)

    async def watch_idle(self) -> None:
        """Run `reap_idle` every `IDLE_CHECK_SECONDS` until cancelled."""
        while True:
            await asyncio.sleep(IDLE_CHECK_SECONDS)
            await self.reap_idle()

    async def close_all(self) -> None:
        """Close every browser (the server is shutting down)."""
        for thread_id in tuple(self.sessions):
            await self.close(thread_id)


async def discard(thread_id: str, event: ServerEvent) -> None:
    """The default `show`: nobody is watching."""
    del thread_id, event


# The thread whose agent run's tools are running; set per run by BrowserRunLink, as DesktopRunLink does for the desktop.
RUN_BROWSER: ContextVar[str | None] = ContextVar("osmo_browser", default=None)


class BrowserRunLink(Addon):
    """Tells the browser tools of an agent run which thread's browser they act on."""

    name = "osmo_browser_link"

    def __init__(self, thread_id: str | None) -> None:
        self.thread_id = thread_id

    async def before_run(self, **payload: Any) -> None:
        """Point the run's browser tools at this thread's browser."""
        RUN_BROWSER.set(self.thread_id)

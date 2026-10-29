"""The agent browser: tools over a fake CDP endpoint, the live widget, the user driving it, and the browser's life with its thread."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeGrok, make_client, xai_key_store
from core_ai import ModelRegistry
from core_ai.providers.base import BaseProvider
from core_ai.types import Message, StreamEvent
from core_harness import EventSink
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from test_agents import ScriptedAgent, Step, start_agent, told
from test_session import call, next_event, say, tool_output
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from symphony_server.browser import RUN_BROWSER, BrowserSessions, browser_tools
from symphony_server.canvas import WidgetBoard
from symphony_server.cdp import BrowserError
from symphony_server.chrome import OWNER_FILE, BrowserUnavailable, kill_group, reap_stale, start_chrome
from symphony_server.protocol import BrowserClick, ServerEvent, WidgetRender, WidgetUpdate
from symphony_server.settings import DEFAULT_APPROVAL_TOOLS, Settings

# Chrome's screencast frames are JPEG by default; these are the start of a JPEG's base64 and nothing more.
FRAMES = ["/9j/AAAA", "/9j/BBBB", "/9j/CCCC"]
# What Chrome reports about every frame's page (the viewport in CSS pixels).
VIEWPORT = {"deviceWidth": 800, "deviceHeight": 600}
PAGE = "http://shop.test/"
STUB = """#!/bin/sh
echo $$ > "$FAKE_CHROME_PID"
# A helper process, as chrome has (renderers, GPU): it must not outlive the browser.
sleep 60 >/dev/null 2>&1 &
echo $! > "$FAKE_CHROME_CHILD"
for arg in "$@"; do
  case "$arg" in --user-data-dir=*) echo "${arg#--user-data-dir=}" > "$FAKE_CHROME_PROFILE" ;; esac
done
case "$FAKE_CHROME_MODE" in
  crash) echo "no display" >&2; exit 1 ;;
  hang) exec sleep 60 ;;
esac
echo "DevTools listening on $FAKE_CHROME_URL" >&2
wait
"""
AX_NODES = [
    {
        "nodeId": "1",
        "role": {"value": "RootWebArea"},
        "name": {"value": "Shop"},
        "backendDOMNodeId": 1,
        "properties": [{"name": "focusable", "value": {"value": True}}],
    },
    {
        "nodeId": "2",
        "role": {"value": "textbox"},
        "name": {"value": "Search products"},
        "backendDOMNodeId": 2,
        "properties": [{"name": "focusable", "value": {"value": True}}],
    },
    {
        "nodeId": "3",
        "role": {"value": "button"},
        "name": {"value": "Buy now"},
        "backendDOMNodeId": 3,
        "properties": [{"name": "focusable", "value": {"value": True}}],
    },
    {"nodeId": "4", "role": {"value": "paragraph"}, "name": {"value": ""}, "backendDOMNodeId": 4, "properties": []},
    {
        "nodeId": "5",
        "ignored": True,
        "role": {"value": "none"},
        "backendDOMNodeId": 5,
        "properties": [{"name": "focusable", "value": {"value": True}}],
    },
]


def process_runs(pid: int) -> bool:
    """The process exists and is not a zombie (a killed process nobody has waited for yet)."""
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip()
    return bool(state) and not state.startswith("Z")


def until(condition: Callable[[], bool]) -> None:
    for _ in range(500):
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


class FakeChrome:
    """A DevTools endpoint plus a stub `chrome` executable that announces it, as a real Chrome does on stderr."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Methods the endpoint takes but never answers, to catch a run mid-call.
        self.stall: set[str] = set()
        self.connections: set[ServerConnection] = set()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        # Profiles go under the test's directory, so stale-profile reaping never looks at the machine's.
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        monkeypatch.setenv("TMPDIR", str(tmp_path))

        async def listen() -> Server:
            return await serve(self.handle, "127.0.0.1", 0)

        server = asyncio.run_coroutine_threadsafe(listen(), self.loop).result(5)
        port = server.sockets[0].getsockname()[1]
        self.pid_file, self.profile_file, self.child_file = tmp_path / "chrome.pid", tmp_path / "chrome.profile", tmp_path / "chrome.child"
        self.executable = tmp_path / "chrome"
        self.executable.write_text(STUB)
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("FAKE_CHROME_URL", f"ws://127.0.0.1:{port}/devtools/browser/fake")
        monkeypatch.setenv("FAKE_CHROME_PID", str(self.pid_file))
        monkeypatch.setenv("FAKE_CHROME_PROFILE", str(self.profile_file))
        monkeypatch.setenv("FAKE_CHROME_CHILD", str(self.child_file))
        monkeypatch.setenv("FAKE_CHROME_MODE", "ok")
        self.server = server

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    def params(self, method: str) -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == method]

    @property
    def pid(self) -> int:
        return int(self.pid_file.read_text())

    @property
    def profile(self) -> Path:
        return Path(self.profile_file.read_text().strip())

    def running(self) -> bool:
        return process_runs(self.pid)

    def child_running(self) -> bool:
        return process_runs(int(self.child_file.read_text()))

    def push_frame(self, data: str) -> None:
        """Chrome shows another frame of its own accord (the page changed); it needs no ack."""
        body = {"method": "Page.screencastFrame", "params": {"data": data, "sessionId": 99, "metadata": VIEWPORT}, "sessionId": "S1"}

        async def send() -> None:
            for connection in self.connections:
                await connection.send(json.dumps(body))

        asyncio.run_coroutine_threadsafe(send(), self.loop).result(5)

    async def handle(self, connection: ServerConnection) -> None:
        self.connections.add(connection)
        try:
            async for raw in connection:
                message = json.loads(raw)
                method, params = message["method"], message["params"]
                self.calls.append((method, params))
                if method in self.stall:
                    continue
                result, after = self.answer(method, params)
                await connection.send(json.dumps({"id": message["id"], "result": result}))
                for event in after:
                    await connection.send(json.dumps(event))
        except ConnectionClosed:
            pass  # the browser's client hung up (the session closed) mid-send
        finally:
            self.connections.discard(connection)

    def answer(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        def event(name: str, body: dict[str, Any]) -> dict[str, Any]:
            return {"method": name, "params": body, "sessionId": "S1"}

        def frame(number: int) -> list[dict[str, Any]]:
            return [event("Page.screencastFrame", {"data": FRAMES[number - 1], "sessionId": number, "metadata": VIEWPORT})] if number <= len(FRAMES) else []

        if method == "Target.createTarget":
            return {"targetId": "T1"}, []
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}, []
        if method == "Page.startScreencast":
            return {}, frame(1)
        if method == "Page.screencastFrameAck":
            return {}, frame(params["sessionId"] + 1)
        if method == "Page.navigate" and params["url"].endswith(".invalid/"):
            return {"frameId": "F1", "errorText": "net::ERR_NAME_NOT_RESOLVED"}, []
        if method == "Page.navigate":
            return {"frameId": "F1", "loaderId": "L1"}, [
                event("Page.frameNavigated", {"frame": {"id": "F1", "url": params["url"]}}),
                event("Page.loadEventFired", {"timestamp": 1}),
            ]
        if method == "Runtime.evaluate" and "location.href" in params["expression"]:
            return {"result": {"type": "object", "value": {"url": PAGE, "title": "Shop", "text": "Boots\n$80"}}}, []
        if method == "Accessibility.getFullAXTree":
            return {"nodes": AX_NODES}, []
        if method == "DOM.getBoxModel":
            return {"model": {"content": [10, 20, 110, 20, 110, 60, 10, 60]}}, []
        return {}, []

    def close(self) -> None:

        async def stop() -> None:
            self.server.close()
            await self.server.wait_closed()

        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def chrome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeChrome]:
    fake = FakeChrome(tmp_path, monkeypatch)
    yield fake
    fake.close()


class Shown:
    """What the browser sessions show on their threads: events by thread id."""

    def __init__(self) -> None:
        self.events: list[tuple[str, ServerEvent]] = []

    async def show(self, thread_id: str, event: ServerEvent) -> None:
        self.events.append((thread_id, event))

    def updates(self) -> list[dict[str, Any]]:
        return [event.patch for _, event in self.events if isinstance(event, WidgetUpdate)]


def sessions_for(chrome: FakeChrome | None) -> tuple[BrowserSessions, Shown]:
    sessions = BrowserSessions(str(chrome.executable) if chrome else None, WidgetBoard())
    shown = Shown()
    sessions.show = shown.show
    return sessions, shown


async def use(sessions: BrowserSessions, name: str, **args: Any) -> dict[str, Any]:
    tool = next(tool for tool in browser_tools(sessions) if tool.name == name)
    result: dict[str, Any] = json.loads(await tool.execute(sink=EventSink(), args=args))
    return result


async def settle(shown: Shown, patch_key: str, count: int) -> None:
    """Wait until `count` updates carrying `patch_key` have been shown."""
    for _ in range(500):
        if sum(patch_key in patch for patch in shown.updates()) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {count} {patch_key!r} updates: {shown.updates()}")


async def test_tools_drive_the_page_and_the_widget_streams_every_frame(chrome: FakeChrome) -> None:
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("thread-1")

    assert await use(sessions, "browser_open", url=PAGE) == {"url": PAGE}
    page = await use(sessions, "browser_read")
    assert page == {
        "url": PAGE,
        "title": "Shop",
        "text": "Boots\n$80",
        "elements": [{"ref": 2, "role": "textbox", "name": "Search products"}, {"ref": 3, "role": "button", "name": "Buy now"}],
    }
    assert await use(sessions, "browser_type", ref=2, target="the search box", text="boots", submit=True) == {"typed_into": "the search box"}
    assert await use(sessions, "browser_click", ref=3, target="Buy now") == {"clicked": "Buy now"}
    assert await use(sessions, "browser_scroll", direction="down") == {"scrolled": "down"}

    # The widget is rendered once, live and empty, then each frame and the page address update it.
    [(thread_id, render)] = [(s, e) for s, e in shown.events if isinstance(e, WidgetRender)]
    assert thread_id == "thread-1" and render.widget.type == "browser" and render.widget.props == {"status": "live"}
    await settle(shown, "frame", 3)
    updates = [(event.id, event.patch) for _, event in shown.events if isinstance(event, WidgetUpdate)]
    assert {widget_id for widget_id, _ in updates} == {render.widget.id}
    assert [patch["frame"] for _, patch in updates if "frame" in patch] == [f"data:image/jpeg;base64,{frame}" for frame in FRAMES]
    assert {"url": PAGE} in [patch for _, patch in updates]
    # Chrome sent frames at its own pace: the next one only after the previous was shown and acked.
    assert [params["sessionId"] for params in chrome.params("Page.screencastFrameAck")] == [1, 2, 3]
    assert chrome.params("Page.startScreencast") == [{}]

    # Clicks land on the middle of the element's box; typing goes to the focused element, then Enter.
    assert chrome.params("DOM.scrollIntoViewIfNeeded") == [{"backendNodeId": 3}]
    clicks = [(p["type"], p["x"], p["y"]) for p in chrome.params("Input.dispatchMouseEvent")]
    assert clicks == [("mousePressed", 60, 40), ("mouseReleased", 60, 40)]
    assert chrome.params("DOM.focus") == [{"backendNodeId": 2}]
    assert chrome.params("Input.insertText") == [{"text": "boots"}]
    assert [p["type"] for p in chrome.params("Input.dispatchKeyEvent")] == ["keyDown", "keyUp"]

    await sessions.close_all()


async def test_tools_report_what_went_wrong_to_the_agent(chrome: FakeChrome) -> None:
    sessions, _ = sessions_for(chrome)
    RUN_BROWSER.set("thread-2")
    assert "only http and https" in (await use(sessions, "browser_open", url="file:///etc/passwd"))["error"]
    assert "ERR_NAME_NOT_RESOLVED" in (await use(sessions, "browser_open", url="http://nowhere.invalid/"))["error"]
    assert "ref" in (await use(sessions, "browser_click", target="the button"))["error"]
    RUN_BROWSER.set(None)
    assert (await use(sessions, "browser_read"))["error"] == "browser tools run inside an agent run of a thread"
    await sessions.close_all()

    unconfigured, _ = sessions_for(None)
    RUN_BROWSER.set("thread-3")
    assert "OSMO_BROWSER_PATH" in (await use(unconfigured, "browser_open", url=PAGE))["error"]


async def test_one_browser_per_thread_and_closing_it_ends_everything(chrome: FakeChrome) -> None:
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("thread-4")
    await use(sessions, "browser_open", url=PAGE)
    await use(sessions, "browser_read")
    assert chrome.methods().count("Target.createTarget") == 1 and len(shown.events) >= 1
    assert chrome.running()
    pid, profile = chrome.pid, chrome.profile
    assert profile.is_dir()
    await settle(shown, "frame", 3)

    await sessions.close("thread-4")
    assert not chrome.running(), f"chrome {pid} still runs"
    assert not chrome.child_running(), "a process chrome started outlived it"
    assert not profile.exists()
    # The widget is left on its last frame and marked ended, once; closing again changes nothing.
    assert shown.updates()[-1] == {"status": "ended"}
    ended = len(shown.events)
    await sessions.close("thread-4")
    assert len(shown.events) == ended


async def test_a_threads_browser_outlives_the_runs_that_use_it(chrome: FakeChrome) -> None:
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("thread-9")
    await use(sessions, "browser_open", url=PAGE)
    first = sessions.sessions["thread-9"]
    await settle(shown, "frame", 3)

    # Another run of the thread (the tools see only the thread) gets the same browser and its page, and no second widget.
    RUN_BROWSER.set(None)
    RUN_BROWSER.set("thread-9")
    assert (await use(sessions, "browser_read"))["url"] == PAGE
    assert sessions.sessions["thread-9"] is first
    assert chrome.methods().count("Target.createTarget") == 1
    assert [type(event) for _, event in shown.events].count(WidgetRender) == 1

    # The widget keeps streaming while no run is using the browser.
    chrome.push_frame("/9j/DDDD")
    await settle(shown, "frame", 4)
    assert shown.updates()[-1] == {"frame": "data:image/jpeg;base64,/9j/DDDD"}

    # Another thread has a browser of its own.
    RUN_BROWSER.set("thread-10")
    await use(sessions, "browser_read")
    assert sessions.sessions["thread-10"] is not first
    await sessions.close_all()
    assert sessions.sessions == {}


async def test_a_browser_that_died_is_replaced_and_its_widget_ended(chrome: FakeChrome) -> None:
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("thread-11")
    await use(sessions, "browser_open", url=PAGE)
    dead = sessions.sessions["thread-11"]
    old_widget = dead.widget_id
    kill_group(dead.process)
    await dead.process.wait()

    await use(sessions, "browser_read")
    assert sessions.sessions["thread-11"] is not dead
    assert chrome.methods().count("Target.createTarget") == 2
    assert [(e.id, e.patch) for _, e in shown.events if isinstance(e, WidgetUpdate) and e.patch == {"status": "ended"}] == [(old_widget, {"status": "ended"})]
    assert chrome.running()
    await sessions.close_all()


# ---------------------------------------------------------------- chrome the server could not close


def dead_pid() -> int:
    """The pid of a process that ran and has been waited for."""
    process = subprocess.Popen(["true"])
    process.wait()
    return process.pid


def profiles(chrome: FakeChrome) -> list[Path]:
    return sorted(chrome.pid_file.parent.glob("osmo-browser-*"))


async def test_chrome_runs_in_its_own_process_group_and_the_group_dies_together(chrome: FakeChrome) -> None:
    process, profile = await start_chrome(str(chrome.executable))
    until(lambda: chrome.child_file.exists() and chrome.child_file.read_text().strip() != "")
    assert os.getpgid(process.pid) == process.pid != os.getpgrp()
    assert (profile / OWNER_FILE).read_text() == f"{os.getpid()} {process.pid}"
    kill_group(process)
    await process.wait()
    assert not chrome.child_running()


def test_a_killed_server_leaves_a_browser_that_the_next_server_reaps(chrome: FakeChrome) -> None:
    server = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import asyncio, sys, time\n"
            "from symphony_server.chrome import start_chrome\n"
            "asyncio.run(start_chrome(sys.argv[1]))\n"
            "print('started', flush=True)\n"
            "time.sleep(60)\n",
            str(chrome.executable),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert server.stdout is not None and server.stdout.readline().strip() == "started"
    until(lambda: chrome.child_file.exists() and chrome.child_file.read_text().strip() != "")
    server.send_signal(signal.SIGKILL)
    server.wait()
    # Chrome is in its own group, so it survived the server: this is the orphan.
    assert chrome.running() and chrome.child_running()
    [profile] = profiles(chrome)

    assert reap_stale() == 1
    until(lambda: not chrome.running() and not chrome.child_running())
    assert not profile.exists()


async def test_a_browser_of_a_running_server_is_not_reaped(chrome: FakeChrome) -> None:
    process, profile = await start_chrome(str(chrome.executable))
    until(lambda: chrome.profile_file.exists() and chrome.profile_file.read_text().strip() != "")
    unowned = chrome.pid_file.parent / "osmo-browser-unowned"
    unowned.mkdir()
    assert reap_stale() == 0
    assert chrome.running() and profile.is_dir() and unowned.is_dir()
    kill_group(process)
    await process.wait()


def test_a_dead_servers_profile_is_removed_without_killing_a_process_that_reuses_its_pid(chrome: FakeChrome) -> None:
    bystander = subprocess.Popen(["sleep", "60"], process_group=0)
    profile = chrome.pid_file.parent / "osmo-browser-stale"
    profile.mkdir()
    (profile / OWNER_FILE).write_text(f"{dead_pid()} {bystander.pid}")
    try:
        assert reap_stale() == 1
        assert not profile.exists()
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


def test_starting_the_server_reaps_what_a_killed_one_left(grok: FakeGrok, chrome: FakeChrome) -> None:
    profile = chrome.pid_file.parent / "osmo-browser-left-behind"
    profile.mkdir()
    (profile / OWNER_FILE).write_text(f"{dead_pid()} {dead_pid()}")
    settings = Settings(xai_base_url=grok.base_url, browser_path=str(chrome.executable), tool_categories=("search", "image", "osmo", "browser"))
    with make_client(settings, secret_store=xai_key_store()):
        assert not profile.exists()


async def test_a_browser_that_will_not_start_is_reported_and_leaves_nothing(chrome: FakeChrome, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CHROME_MODE", "crash")
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("thread-5")
    result = await use(sessions, "browser_open", url=PAGE)
    assert "no display" in result["error"]
    assert not chrome.profile.exists()
    assert not chrome.child_running()
    assert shown.events == []
    await sessions.close_all()


async def test_cancelling_a_run_while_its_browser_starts_kills_chrome(chrome: FakeChrome, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CHROME_MODE", "hang")  # chrome never says it is listening
    sessions, _ = sessions_for(chrome)
    RUN_BROWSER.set("thread-6")
    opening = asyncio.create_task(use(sessions, "browser_open", url=PAGE))
    for _ in range(500):
        if chrome.pid_file.exists() and chrome.profile_file.exists():
            break
        await asyncio.sleep(0.01)
    assert chrome.running()
    pid, profile = chrome.pid, chrome.profile
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert not chrome.running(), f"chrome {pid} still runs"
    assert not chrome.child_running()
    assert not profile.exists()
    assert sessions.sessions == {}


async def test_cancelling_a_run_after_its_widget_is_shown_freezes_the_widget(chrome: FakeChrome) -> None:
    chrome.stall.add("Page.startScreencast")
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("thread-8")
    opening = asyncio.create_task(use(sessions, "browser_open", url=PAGE))
    for _ in range(500):
        if "Page.startScreencast" in chrome.methods():
            break
        await asyncio.sleep(0.01)
    assert [type(event) for _, event in shown.events] == [WidgetRender]
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert shown.updates() == [{"status": "ended"}]
    assert not chrome.running()
    assert not chrome.profile.exists()


async def test_a_missing_executable_is_an_error_for_the_agent(tmp_path: Path) -> None:
    sessions = BrowserSessions(str(tmp_path / "no-such-chrome"), WidgetBoard())
    with pytest.raises(BrowserUnavailable, match="could not start the browser"):
        await sessions.open("thread-7")


# ---------------------------------------------------------------- through a real agent run


class HoldingAgent(ScriptedAgent):
    """A scripted agent that waits at its second model call (after its first tool ran) until `release` is set."""

    def __init__(self, steps: list[Step]) -> None:
        super().__init__(steps)
        self.release = threading.Event()
        self.calls = 0

    async def stream(
        self,
        model_name: str,
        messages: list[Message],
        tools: list[dict[str, Any]] = [],  # noqa: B006 - BaseProvider's signature
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.calls += 1
        if self.calls == 2:
            await asyncio.to_thread(self.release.wait, 10)
        async for event in super().stream(model_name, messages, tools, max_output_tokens, reasoning_effort, extra_headers):
            yield event


def open_step(url: str = PAGE) -> Step:
    return Step(tool="browser_open", arguments=json.dumps({"url": url}))


def browser_client(grok: FakeGrok, agent: BaseProvider, chrome: FakeChrome, approval_tools: tuple[str, ...] = DEFAULT_APPROVAL_TOOLS) -> TestClient:
    registry = ModelRegistry()
    settings = Settings(
        xai_base_url=grok.base_url,
        approval_tools=approval_tools,
        browser_path=str(chrome.executable),
        tool_categories=("search", "image", "osmo", "browser"),
    )
    client = make_client(settings, secret_store=xai_key_store(), registry=registry)
    registry.register("grok", agent)
    return client


def until_run(ws: WebSocketTestSession, status: str) -> list[dict[str, Any]]:
    """Every event up to the point where an agent run reaches `status`."""
    seen: list[dict[str, Any]] = []
    while True:
        message = ws.receive()
        if message.get("text") is None:
            continue
        event: dict[str, Any] = json.loads(message["text"])
        seen.append(event)
        if event["type"] == "agent.update" and event["status"] == status:
            return seen


def until_ended(ws: WebSocketTestSession, widget_id: str) -> list[dict[str, Any]]:
    """Every event up to the point where the widget is marked ended."""
    seen: list[dict[str, Any]] = []
    while not (seen and seen[-1]["type"] == "widget.update" and seen[-1]["id"] == widget_id and seen[-1]["patch"] == {"status": "ended"}):
        message = ws.receive()
        if message.get("text") is not None:
            seen.append(json.loads(message["text"]))
    return seen


def widget_events(events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The browser widget's render, and the patches shown for it after."""
    [render] = [e for e in events if e["type"] == "widget.render"]
    assert render["widget"]["type"] == "browser" and render["widget"]["props"] == {"status": "live"}
    return render["widget"], [e["patch"] for e in events if e["type"] == "widget.update" and e["id"] == render["widget"]["id"]]


def browser_up(chrome: FakeChrome) -> bool:
    return chrome.running() and chrome.child_running() and chrome.profile.is_dir()


def assert_browser_gone(chrome: FakeChrome) -> None:
    assert not chrome.running() and not chrome.child_running()
    assert not chrome.profile.exists()


def thread_of(client: TestClient) -> str:
    thread_id: str = client.get("/api/threads").json()["threads"][0]["id"]
    return thread_id


def user_sends(ws: WebSocketTestSession, **event: Any) -> None:
    ws.send_text(json.dumps(event))


def test_a_run_ending_leaves_its_browser_up_and_streaming_and_the_next_run_reuses_it(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = ScriptedAgent([open_step(), Step(text="The boots cost $80."), Step(tool="browser_read"), Step(text="Same page.")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        first_run = start_agent(ws, grok, "find boots")
        first = until_run(ws, "completed")
        widget, patches = widget_events(first)
        assert {"status": "ended"} not in patches
        assert browser_up(chrome)

        # Between runs the widget keeps streaming.
        chrome.push_frame("/9j/DDDD")
        while (patch := next_event(ws, "widget.update")["patch"]).get("frame") != "data:image/jpeg;base64,/9j/DDDD":
            assert patch != {"status": "ended"}

        # The next run of the thread gets the same browser: no new chrome, no second widget.
        told(grok, f"[agent {first_run}] done")
        say(grok, "Look at it again.")
        call(grok, "run_agent", {"task": "read the page"}, "a2", "r2")
        assert tool_output(grok)["status"] == "running"
        second = until_run(ws, "completed")
        assert [e for e in second if e["type"] == "widget.render"] == []
        assert {"status": "ended"} not in [e.get("patch") for e in second]
        assert chrome.methods().count("Target.createTarget") == 1 and "Accessibility.getFullAXTree" in chrome.methods()
        assert widget["id"]
        assert browser_up(chrome)
    # The server shutting down ends it.
    assert_browser_gone(chrome)


def test_a_failed_or_cancelled_run_leaves_its_browser_up(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = HoldingAgent([open_step(), Step(text="never reached")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        run_id = start_agent(ws, grok, "find boots")
        next_event(ws, "widget.render")
        assert chrome.running()
        say(grok, "Never mind, stop it.")
        call(grok, "cancel_agent", {"run_id": run_id}, "c1", "r2")
        tool_output(grok)
        events = until_run(ws, "cancelled")
        assert {"status": "ended"} not in [e.get("patch") for e in events]
        assert browser_up(chrome)
        agent.release.set()  # let the abandoned model call return before the server shuts down
    assert_browser_gone(chrome)


def test_a_failed_run_leaves_its_browser_up(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = HoldingAgent([open_step()])  # no second step: the next model call raises
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "find boots")
        next_event(ws, "widget.render")
        agent.release.set()
        events = until_run(ws, "failed")
        assert {"status": "ended"} not in [e.get("patch") for e in events]
        assert browser_up(chrome)
    assert_browser_gone(chrome)


def test_a_server_shutdown_closes_the_browsers_of_running_runs(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = HoldingAgent([open_step(), Step(text="never reached")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "find boots")
        next_event(ws, "widget.render")
        assert chrome.running()
        # The run's model call is let go only after the server has started shutting down.
        threading.Timer(0.3, agent.release.set).start()
    assert_browser_gone(chrome)


def test_the_end_of_the_session_closes_the_threads_browser(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = ScriptedAgent([open_step(), Step(text="done")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "find boots")
        widget, _ = widget_events(until_run(ws, "completed"))
        assert browser_up(chrome)
        user_sends(ws, type="session.end")
        frames = until_ended(ws, widget["id"])
        assert frames[-1]["patch"] == {"status": "ended"}
        assert_browser_gone(chrome)


def test_the_user_can_close_the_browser_from_the_widget(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = ScriptedAgent([open_step(), Step(text="done")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "find boots")
        widget, _ = widget_events(until_run(ws, "completed"))
        # Some other widget's id changes nothing.
        user_sends(ws, type="browser.close", id="w-other")
        time.sleep(0.2)
        assert browser_up(chrome)
        user_sends(ws, type="browser.close", id=widget["id"])
        until_ended(ws, widget["id"])
        assert_browser_gone(chrome)


def test_a_client_that_connects_again_gets_the_browser_widget_back_as_it_is(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = ScriptedAgent([open_step(), Step(text="done")])
    with browser_client(grok, agent, chrome) as client:
        with client.websocket_connect("/ws") as first:
            start_agent(first, grok, "find boots")
            widget, patches = widget_events(until_run(first, "completed"))
            thread_id = thread_of(client)
        # The page went away (a reload, a lost connection): without session.end the browser stays.
        assert browser_up(chrome)

        with client.websocket_connect("/ws") as second:
            user_sends(second, type="session.start", voice="eve", threadId=thread_id)
            restored = next_event(second, "widget.render")
            assert restored["widget"]["id"] == widget["id"] and restored["widget"]["type"] == "browser"
            props = restored["widget"]["props"]
            assert props["status"] == "live" and props["url"] == PAGE
            assert props["frame"].startswith("data:image/jpeg;base64,/9j/")
            # Another thread (a new one) is not shown this browser.
        with client.websocket_connect("/ws") as other:
            user_sends(other, type="session.start", voice="eve")
            assert next_event(other, "thread")["thread"]["id"] != thread_id
            other.send_text(json.dumps({"type": "session.end"}))
            time.sleep(0.2)
            assert browser_up(chrome)


def test_the_user_clicks_scrolls_and_types_into_the_page_without_approval(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = HoldingAgent([open_step(), Step(text="done")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "find boots")
        render = next_event(ws, "widget.render")["widget"]
        widget_id = render["id"]
        while "frame" not in next_event(ws, "widget.update")["patch"]:  # the user sees a page before they can touch it
            pass
        # The page the user sees is 800 x 600 CSS pixels (what chrome reported with the frames): fractions map onto it.
        user_sends(ws, type="browser.click", id="w-other", x=0.1, y=0.1)  # not this thread's browser: ignored
        user_sends(ws, type="browser.click", id=widget_id, x=0.5, y=0.25)
        user_sends(ws, type="browser.scroll", id=widget_id, x=0.25, y=0.5, deltaX=0, deltaY=120)
        user_sends(ws, type="browser.key", id=widget_id, key="a", code="KeyA", keyCode=65)
        user_sends(ws, type="browser.key", id=widget_id, key="Backspace", code="Backspace", keyCode=8)
        user_sends(ws, type="browser.key", id=widget_id, key="Enter", code="Enter", keyCode=13)
        until(lambda: len(chrome.params("Input.dispatchKeyEvent")) == 6)

        mouse = [(p["type"], p["x"], p["y"]) for p in chrome.params("Input.dispatchMouseEvent")]
        assert mouse == [("mousePressed", 400, 150), ("mouseReleased", 400, 150), ("mouseWheel", 200, 300)]
        assert chrome.params("Input.dispatchMouseEvent")[2]["deltaY"] == 120
        keys = [(p["type"], p["key"], p.get("text"), p["windowsVirtualKeyCode"]) for p in chrome.params("Input.dispatchKeyEvent")]
        assert keys == [
            ("keyDown", "a", "a", 65),
            ("keyUp", "a", None, 65),
            ("rawKeyDown", "Backspace", None, 8),
            ("keyUp", "Backspace", None, 8),
            ("keyDown", "Enter", "\r", 13),
            ("keyUp", "Enter", None, 13),
        ]
        # browser_click and browser_type are approval tools, yet nobody approved anything: the user's own input goes straight to the page.
        agent.release.set()
        assert {event["type"] for event in until_run(ws, "completed")} & {"approval.request"} == set()


def test_input_the_protocol_does_not_allow_never_reaches_the_page(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = HoldingAgent([open_step(), Step(text="done")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "find boots")
        widget_id = next_event(ws, "widget.render")["widget"]["id"]
        user_sends(ws, type="browser.click", id=widget_id, x=2, y=0.5)  # outside the displayed page
        user_sends(ws, type="browser.key", id=widget_id, key="", code="", keyCode=0)
        user_sends(ws, type="browser.click", id=widget_id, x=0.5, y=0.5, extra=True)
        user_sends(ws, type="browser.close", id=widget_id, extra=True)
        time.sleep(0.3)
        assert chrome.params("Input.dispatchMouseEvent") == [] and chrome.params("Input.dispatchKeyEvent") == []
        assert browser_up(chrome)
        agent.release.set()


async def test_the_user_cannot_point_at_a_page_that_has_shown_no_frame(chrome: FakeChrome) -> None:
    sessions, _ = sessions_for(chrome)
    RUN_BROWSER.set("thread-12")
    await use(sessions, "browser_open", url=PAGE)
    session = sessions.sessions["thread-12"]
    session.viewport = None
    with pytest.raises(BrowserError, match="no frame"):
        await session.drive(BrowserClick(type="browser.click", id=session.widget_id, x=0.5, y=0.5))
    await sessions.close_all()


def test_clicking_and_typing_wait_for_approval_but_reading_does_not(grok: FakeGrok, chrome: FakeChrome) -> None:
    click = json.dumps({"ref": 3, "target": "the Buy now button"})
    agent = ScriptedAgent(
        [
            open_step(),
            Step(tool="browser_read"),
            Step(tool="browser_click", arguments=click),
            Step(tool="browser_click", arguments=click),
            Step(text="Done."),
        ]
    )
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "buy boots")
        asked = next_event(ws, "approval.request")
        assert asked["tool"] == "browser_click" and asked["summary"] == "Clicking · the Buy now button"
        assert json.loads(asked["arguments"]) == {"ref": 3, "target": "the Buy now button"}
        # Opening and reading ran without asking; the click has not happened.
        assert {"Page.navigate", "Accessibility.getFullAXTree"} <= set(chrome.methods())
        assert chrome.params("Input.dispatchMouseEvent") == []

        assert client.post(f"/api/approvals/{asked['approvalId']}", json={"approved": False}).status_code == 204
        second = next_event(ws, "approval.request")
        assert chrome.params("Input.dispatchMouseEvent") == []
        assert client.post(f"/api/approvals/{second['approvalId']}", json={"approved": True}).status_code == 204
        until_run(ws, "completed")
        assert [p["type"] for p in chrome.params("Input.dispatchMouseEvent")] == ["mousePressed", "mouseReleased"]


def test_without_a_browser_path_the_tools_say_so(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = ScriptedAgent([open_step(), Step(text="I could not browse.")])
    registry = ModelRegistry()
    settings = Settings(xai_base_url=grok.base_url, tool_categories=("search", "browser"))
    with make_client(settings, secret_store=xai_key_store(), registry=registry) as client:
        registry.register("grok", agent)
        with client.websocket_connect("/ws") as ws:
            start_agent(ws, grok, "find boots")
            update = next_event(ws, "agent.update")
            while update["status"] != "completed":
                update = next_event(ws, "agent.update")
            assert chrome.methods() == []

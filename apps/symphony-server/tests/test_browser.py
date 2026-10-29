"""The agent browser: tools over a fake CDP endpoint, the live widget, and the browser's life with its run."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from collections.abc import AsyncGenerator, Iterator
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
from test_agents import ScriptedAgent, Step, start_agent
from test_session import call, next_event, say, tool_output
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from symphony_server.browser import RUN_BROWSER, BrowserSessions, BrowserUnavailable, browser_tools
from symphony_server.canvas import WidgetBoard
from symphony_server.protocol import ServerEvent, WidgetRender, WidgetUpdate
from symphony_server.settings import DEFAULT_APPROVAL_TOOLS, Settings

# Chrome's screencast frames are JPEG by default; these are the start of a JPEG's base64 and nothing more.
FRAMES = ["/9j/AAAA", "/9j/BBBB", "/9j/CCCC"]
PAGE = "http://shop.test/"
STUB = """#!/bin/sh
echo $$ > "$FAKE_CHROME_PID"
for arg in "$@"; do
  case "$arg" in --user-data-dir=*) echo "${arg#--user-data-dir=}" > "$FAKE_CHROME_PROFILE" ;; esac
done
case "$FAKE_CHROME_MODE" in
  crash) echo "no display" >&2; exit 1 ;;
  hang) exec sleep 60 ;;
esac
echo "DevTools listening on $FAKE_CHROME_URL" >&2
exec sleep 60
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


class FakeChrome:
    """A DevTools endpoint plus a stub `chrome` executable that announces it, as a real Chrome does on stderr."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Methods the endpoint takes but never answers, to catch a run mid-call.
        self.stall: set[str] = set()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

        async def listen() -> Server:
            return await serve(self.handle, "127.0.0.1", 0)

        server = asyncio.run_coroutine_threadsafe(listen(), self.loop).result(5)
        port = server.sockets[0].getsockname()[1]
        self.pid_file, self.profile_file = tmp_path / "chrome.pid", tmp_path / "chrome.profile"
        self.executable = tmp_path / "chrome"
        self.executable.write_text(STUB)
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("FAKE_CHROME_URL", f"ws://127.0.0.1:{port}/devtools/browser/fake")
        monkeypatch.setenv("FAKE_CHROME_PID", str(self.pid_file))
        monkeypatch.setenv("FAKE_CHROME_PROFILE", str(self.profile_file))
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
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        return True

    async def handle(self, connection: ServerConnection) -> None:
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

    def answer(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        def event(name: str, body: dict[str, Any]) -> dict[str, Any]:
            return {"method": name, "params": body, "sessionId": "S1"}

        def frame(number: int) -> list[dict[str, Any]]:
            return [event("Page.screencastFrame", {"data": FRAMES[number - 1], "sessionId": number, "metadata": {}})] if number <= len(FRAMES) else []

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
    """What the browser sessions show on the thread: events by run session id."""

    def __init__(self) -> None:
        self.events: list[tuple[str, ServerEvent]] = []

    async def show(self, session_id: str, event: ServerEvent) -> None:
        self.events.append((session_id, event))

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
    RUN_BROWSER.set("agent-1")

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
    [(session_id, render)] = [(s, e) for s, e in shown.events if isinstance(e, WidgetRender)]
    assert session_id == "agent-1" and render.widget.type == "browser" and render.widget.props == {"status": "live"}
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
    RUN_BROWSER.set("agent-2")
    assert "only http and https" in (await use(sessions, "browser_open", url="file:///etc/passwd"))["error"]
    assert "ERR_NAME_NOT_RESOLVED" in (await use(sessions, "browser_open", url="http://nowhere.invalid/"))["error"]
    assert "ref" in (await use(sessions, "browser_click", target="the button"))["error"]
    RUN_BROWSER.set(None)
    assert (await use(sessions, "browser_read"))["error"] == "browser tools run inside an agent run"
    await sessions.close_all()

    unconfigured, _ = sessions_for(None)
    RUN_BROWSER.set("agent-3")
    assert "OSMO_BROWSER_PATH" in (await use(unconfigured, "browser_open", url=PAGE))["error"]


async def test_one_browser_per_run_and_closing_it_ends_everything(chrome: FakeChrome) -> None:
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("agent-4")
    await use(sessions, "browser_open", url=PAGE)
    await use(sessions, "browser_read")
    assert chrome.methods().count("Target.createTarget") == 1 and len(shown.events) >= 1
    assert chrome.running()
    pid, profile = chrome.pid, chrome.profile
    assert profile.is_dir()
    await settle(shown, "frame", 3)

    await sessions.close("agent-4")
    assert not chrome.running(), f"chrome {pid} still runs"
    assert not profile.exists()
    # The widget is left on its last frame and marked ended, once; closing again changes nothing.
    assert shown.updates()[-1] == {"status": "ended"}
    ended = len(shown.events)
    await sessions.close("agent-4")
    assert len(shown.events) == ended


async def test_a_browser_that_will_not_start_is_reported_and_leaves_nothing(chrome: FakeChrome, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CHROME_MODE", "crash")
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("agent-5")
    result = await use(sessions, "browser_open", url=PAGE)
    assert "no display" in result["error"]
    assert not chrome.profile.exists()
    assert shown.events == []
    await sessions.close_all()


async def test_cancelling_a_run_while_its_browser_starts_kills_chrome(chrome: FakeChrome, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CHROME_MODE", "hang")  # chrome never says it is listening
    sessions, _ = sessions_for(chrome)
    RUN_BROWSER.set("agent-6")
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
    assert not profile.exists()
    assert sessions.sessions == {}


async def test_cancelling_a_run_after_its_widget_is_shown_freezes_the_widget(chrome: FakeChrome) -> None:
    chrome.stall.add("Page.startScreencast")
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("agent-8")
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
        await sessions.open("agent-7")


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


def until_finished(ws: WebSocketTestSession, status: str) -> list[dict[str, Any]]:
    """Every event up to the point where the run reached `status` and its browser widget ended (in either order)."""
    seen: list[dict[str, Any]] = []
    run_done = browser_ended = False
    while not (run_done and browser_ended):
        message = ws.receive()
        if message.get("text") is None:
            continue
        event: dict[str, Any] = json.loads(message["text"])
        seen.append(event)
        run_done = run_done or (event["type"] == "agent.update" and event["status"] == status)
        browser_ended = browser_ended or (event["type"] == "widget.update" and event["patch"] == {"status": "ended"})
    return seen


def assert_browser_ended(events: list[dict[str, Any]], chrome: FakeChrome) -> list[str]:
    """The browser widget was rendered live, ended last, and chrome and its profile are gone; returns its frames."""
    [render] = [e for e in events if e["type"] == "widget.render"]
    assert render["widget"]["type"] == "browser" and render["widget"]["props"] == {"status": "live"}
    patches = [e["patch"] for e in events if e["type"] == "widget.update" and e["id"] == render["widget"]["id"]]
    assert patches[-1] == {"status": "ended"}
    assert not chrome.running()
    assert not chrome.profile.exists()
    return [p["frame"] for p in patches if "frame" in p]


def test_a_completed_run_streams_its_browser_and_closes_it(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = HoldingAgent([open_step(), Step(text="The boots cost $80.")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "find boots")
        # While the run waits for its model, chrome is up and its frames reach the canvas.
        seen = [next_event(ws, "widget.render")]
        assert chrome.running()
        while seen[-1].get("patch", {}).get("frame") != f"data:image/jpeg;base64,{FRAMES[-1]}":
            seen.append(next_event(ws, "widget.update"))
        agent.release.set()
        frames = assert_browser_ended([*seen, *until_finished(ws, "completed")], chrome)
        assert frames == [f"data:image/jpeg;base64,{frame}" for frame in FRAMES]


def test_a_failed_run_closes_its_browser(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = HoldingAgent([open_step()])  # no second step: the next model call raises
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "find boots")
        render = next_event(ws, "widget.render")
        assert chrome.running()
        agent.release.set()
        assert_browser_ended([render, *until_finished(ws, "failed")], chrome)


def test_a_cancelled_run_closes_its_browser(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = HoldingAgent([open_step(), Step(text="never reached")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        run_id = start_agent(ws, grok, "find boots")
        render = next_event(ws, "widget.render")
        assert chrome.running()
        say(grok, "Never mind, stop it.")
        call(grok, "cancel_agent", {"run_id": run_id}, "c1", "r2")
        tool_output(grok)
        assert_browser_ended([render, *until_finished(ws, "cancelled")], chrome)
        agent.release.set()  # let the abandoned model call return before the server shuts down


def test_a_server_shutdown_closes_the_browsers_of_running_runs(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = HoldingAgent([open_step(), Step(text="never reached")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "find boots")
        next_event(ws, "widget.render")
        assert chrome.running()
        # The run's model call is let go only after the server has started shutting down.
        threading.Timer(0.3, agent.release.set).start()
    assert not chrome.running()
    assert not chrome.profile.exists()


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
        until_finished(ws, "completed")
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

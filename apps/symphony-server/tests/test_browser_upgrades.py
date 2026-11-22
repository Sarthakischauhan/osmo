"""The agent browser's upgrades: finding Chrome, Osmo's profile and a visible window, the extra actions, secret
fields kept from the model, per-site rules (in the tools and in approval) and the user taking the browser over."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeGrok
from test_agents import ScriptedAgent, Step, start_agent, told
from test_browser import (
    AX_NODES,
    PAGE,
    FakeChrome,
    browser_client,
    chrome,
    dead_pid,
    next_event,
    open_step,
    sessions_for,
    until,
    until_run,
    use,
    user_sends,
)
from test_session import call, say, tool_output

from symphony_server.approvals import APPROVAL_REQUIRED, APPROVAL_RESOLVED, ApprovalAddon, ApprovalBroker
from symphony_server.browser_rules import DomainRules
from symphony_server.browser_sessions import RUN_BROWSER, BrowserSessions
from symphony_server.browser_tools import BrowserApprovalPolicy
from symphony_server.canvas import WidgetBoard
from symphony_server.chrome import OWNER_FILE, find_chrome, reap_stale
from symphony_server.settings import Settings, browser_profile

__all__ = ["chrome"]  # the fixture, shared with test_browser

LOGIN_NODES = [
    *AX_NODES,
    {
        "nodeId": "6",
        "role": {"value": "textbox"},
        "name": {"value": "Password"},
        "backendDOMNodeId": 6,
        "properties": [{"name": "focusable", "value": {"value": True}}],
    },
    {
        "nodeId": "7",
        "role": {"value": "textbox"},
        "name": {"value": "Card number"},
        "backendDOMNodeId": 7,
        "properties": [{"name": "focusable", "value": {"value": True}}],
    },
]
DESCRIBED = {
    2: {"nodeName": "INPUT", "attributes": ["type", "search", "name", "q"]},
    6: {"nodeName": "INPUT", "attributes": ["type", "password", "name", "pw"]},
    7: {"nodeName": "INPUT", "attributes": ["type", "text", "autocomplete", "billing cc-number"]},
}


# ------------------------------------------------------------ finding Chrome


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_find_chrome_prefers_an_installed_mac_app_then_path_then_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Avoid reading the real machine's /Applications while checking browser priority.
    from symphony_server import chrome as chrome_module

    monkeypatch.setattr(chrome_module, "browser_candidates", lambda platform, home: [home / "Applications" / name for name in chrome_module.MAC_BROWSERS] if platform == "darwin" else [])
    on_path = {"chromium": "/usr/bin/chromium"}
    # Linux: the first browser on PATH, in Osmo's order.
    assert find_chrome("linux", tmp_path, on_path.get) == "/usr/bin/chromium"
    assert find_chrome("linux", tmp_path, {}.get) is None
    # macOS: an app in ~/Applications (Chrome first, over Edge) beats PATH; a file that cannot run does not count.
    edge = executable(tmp_path / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    assert find_chrome("darwin", tmp_path, on_path.get) == str(edge)
    chrome_app = executable(tmp_path / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    assert find_chrome("darwin", tmp_path, on_path.get) == str(chrome_app)
    chrome_app.chmod(0o644)
    edge.chmod(0o644)
    assert find_chrome("darwin", tmp_path, on_path.get) == "/usr/bin/chromium"


def test_the_profile_setting(tmp_path: Path) -> None:
    assert browser_profile(None) is None and browser_profile("") is None and browser_profile("0") is None
    assert browser_profile("1") == Path("~/.config/osmo/browser-profile").expanduser()
    assert browser_profile(str(tmp_path)) == tmp_path


# ------------------------------------------------------------ profile and window


async def test_osmos_profile_is_kept_and_serves_one_browser_at_a_time_and_visible_shows_a_window(chrome: FakeChrome, tmp_path: Path) -> None:
    kept = tmp_path / "osmo-profile"
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard(), profile=kept, visible=True)
    RUN_BROWSER.set("thread-a")
    await use(sessions, "browser_open", url=PAGE)
    args = chrome.args_file.read_text()
    assert f"--user-data-dir={kept}" in args and "--headless" not in args
    assert (kept / OWNER_FILE).is_file()

    # A second thread while the first holds the profile: a throwaway one (Chrome locks a profile to one process).
    RUN_BROWSER.set("thread-b")
    await use(sessions, "browser_read")
    second = sessions.sessions["thread-b"]
    assert second.profile != kept and not second.keep_profile

    await sessions.close_all()
    # Logins live on: the kept profile stays (without its owner file), the throwaway one is gone.
    assert kept.is_dir() and not (kept / OWNER_FILE).exists()
    assert not second.profile.exists()


async def test_headless_by_default(chrome: FakeChrome) -> None:
    sessions, _ = sessions_for(chrome)
    RUN_BROWSER.set("thread-h")
    await use(sessions, "browser_read")
    assert "--headless=new" in chrome.args_file.read_text()
    await sessions.close_all()


def test_reaping_stops_a_dead_servers_chrome_on_osmos_profile_but_keeps_the_profile(tmp_path: Path) -> None:
    kept = tmp_path / "osmo-profile"
    kept.mkdir()
    (kept / "Cookies").write_text("logins")
    (kept / OWNER_FILE).write_text(f"{dead_pid()} {dead_pid()}")
    assert reap_stale(kept) == 1
    assert (kept / "Cookies").read_text() == "logins" and not (kept / OWNER_FILE).exists()
    # A profile of a live server is left alone.
    (kept / OWNER_FILE).write_text(f"{os.getpid()} {os.getpid()}")
    assert reap_stale(kept) == 0


# ------------------------------------------------------------ more actions


async def test_back_forward_press_select_and_wait(chrome: FakeChrome) -> None:
    history = {"currentIndex": 1, "entries": [{"id": 10, "url": "http://shop.test/a"}, {"id": 11, "url": PAGE}]}
    polls: list[str] = []

    def evaluate(params: dict[str, Any]) -> dict[str, Any]:
        if "location.href" in params["expression"]:
            return {"result": {"value": {"url": PAGE, "title": "Shop", "text": "Boots"}}}
        polls.append(params["expression"])
        return {"result": {"value": len(polls) >= 2}}

    def call_on(params: dict[str, Any]) -> dict[str, Any]:
        wanted = params["arguments"][0]["value"]
        return {"result": {"value": {"selected": "Size 42"} if wanted == "42" else {"error": "no option", "options": ["Size 42"]}}}

    chrome.answers.update(
        {
            "Page.getNavigationHistory": lambda params: history,
            "DOM.resolveNode": {"object": {"objectId": "obj-1"}},
            "Runtime.callFunctionOn": call_on,
            "Runtime.evaluate": evaluate,
        },
    )
    sessions, _ = sessions_for(chrome)
    RUN_BROWSER.set("thread-act")

    # Back goes to the entry before the current one; forward past the last is an error the agent reads.
    assert await use(sessions, "browser_back") == {"url": PAGE}
    assert chrome.params("Page.navigateToHistoryEntry") == [{"entryId": 10}]
    assert "no page to go forward" in (await use(sessions, "browser_forward"))["error"]

    assert await use(sessions, "browser_press", key="Escape", target="close the menu") == {"pressed": "Escape"}
    assert [(p["type"], p["key"], p["windowsVirtualKeyCode"]) for p in chrome.params("Input.dispatchKeyEvent")] == [
        ("rawKeyDown", "Escape", 27),
        ("keyUp", "Escape", 27),
    ]
    assert "error" in await use(sessions, "browser_press", key="F12", target="devtools")

    assert await use(sessions, "browser_select", ref=4, option="42") == {"selected": "Size 42"}
    assert "Size 42" in (await use(sessions, "browser_select", ref=4, option="45"))["error"]

    assert await use(sessions, "browser_wait", text='He said "hi"', seconds=2) == {"found": True}
    assert polls[0].endswith('.includes("He said \\"hi\\"")')
    assert "error" in await use(sessions, "browser_wait", text="x", seconds=60)
    await sessions.close_all()


# ------------------------------------------------------------ secret fields


async def test_password_and_card_fields_are_marked_and_never_typed_into(chrome: FakeChrome) -> None:
    chrome.answers.update(
        {
            "Accessibility.getFullAXTree": {"nodes": LOGIN_NODES},
            "DOM.describeNode": lambda params: {"node": DESCRIBED.get(params["backendNodeId"], {"nodeName": "BUTTON"})},
        },
    )
    sessions, _ = sessions_for(chrome)
    RUN_BROWSER.set("thread-login")
    page = await use(sessions, "browser_read")
    assert page["elements"] == [
        {"ref": 2, "role": "textbox", "name": "Search products"},
        {"ref": 3, "role": "button", "name": "Buy now"},
        {"ref": 6, "role": "textbox", "name": "Password", "sensitive": "password"},
        {"ref": 7, "role": "textbox", "name": "Card number", "sensitive": "card"},
    ]
    # Only fields that hold text are checked, not buttons.
    assert sorted(p["backendNodeId"] for p in chrome.params("DOM.describeNode")) == [2, 6, 7]

    for ref, kind in ((6, "password"), (7, "card")):
        refused = await use(sessions, "browser_type", ref=ref, target="the field", text="hunter2")
        assert f"{kind} field" in refused["error"] and "take over" in refused["error"]
    # Nothing was focused or typed: the secret never reached the page, nor anything sent by the model about it.
    assert chrome.params("Input.insertText") == [] and chrome.params("DOM.focus") == []
    assert await use(sessions, "browser_type", ref=2, target="search", text="boots") == {"typed_into": "search"}
    await sessions.close_all()


# ------------------------------------------------------------ site rules


def test_domain_rules_match_sites_and_subdomains_most_specific_first() -> None:
    rules = DomainRules.parse("example.com=allow, pay.example.com=deny, *.github.com=ask, GitHub.com=Allow")
    assert rules.decide("https://example.com/a") == "allow"
    assert rules.decide("https://shop.example.com/") == "allow"
    assert rules.decide("https://pay.example.com/checkout") == "deny"
    assert rules.decide("https://a.pay.example.com/") == "deny"
    assert rules.decide("https://github.com/") == "allow"
    assert rules.decide("https://gist.github.com/") == "ask"
    # No rule, no host, or a look-alike: the default.
    assert rules.match("https://notexample.com/") is None and rules.decide("https://notexample.com/") == "ask"
    assert rules.match("about:blank") is None and rules.match(None) is None
    assert DomainRules.parse(None).rules == {} and DomainRules.parse("").rules == {}
    for bad in ("example.com", "example.com=maybe", "exa mple.com=allow", "https://x.com=allow", "*=deny"):
        with pytest.raises(ValueError):
            DomainRules.parse(bad)


async def test_a_denied_site_cannot_be_opened_or_reached_by_a_redirect(chrome: FakeChrome) -> None:
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard(), rules=DomainRules.parse("bank.test=deny"))
    RUN_BROWSER.set("thread-deny")
    assert "blocked" in (await use(sessions, "browser_open", url="https://www.bank.test/login"))["error"]
    assert [p["url"] for p in chrome.params("Page.navigate")] == []

    # shop.test redirects to the bank: the redirect hop is refused before Chrome requests it.
    chrome.redirects[PAGE] = "https://bank.test/"
    assert "bank.test is blocked by the user's site rules" in (await use(sessions, "browser_open", url=PAGE))["error"]
    assert [p["url"] for p in chrome.params("Page.navigate")] == [PAGE]
    assert len(chrome.params("Fetch.continueRequest")) == 1 and len(chrome.params("Fetch.failRequest")) == 1

    # A page that moves to the bank by itself (a script, a meta refresh) is left at once.
    chrome.redirects.clear()
    chrome.answers["Runtime.evaluate"] = lambda params: {"result": {"value": {"url": "https://bank.test/" if len(chrome.params("Page.navigate")) == 2 else PAGE, "title": "", "text": ""}}}
    assert "bank.test" in (await use(sessions, "browser_open", url=PAGE))["error"]
    assert [p["url"] for p in chrome.params("Page.navigate")] == [PAGE, PAGE, "about:blank"]
    await sessions.close_all()


async def test_on_a_denied_page_every_tool_refuses(chrome: FakeChrome) -> None:
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard(), rules=DomainRules.parse("shop.test=deny"))
    RUN_BROWSER.set("thread-on-denied")
    session = await sessions.open("thread-on-denied")
    await session.patch({"url": PAGE})  # the user went there from the widget
    assert "not allowed" in (await use(sessions, "browser_read"))["error"]
    assert chrome.params("Accessibility.getFullAXTree") == []
    await sessions.close_all()


class Emitted:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))


async def approval_for(addon: ApprovalAddon, broker: ApprovalBroker, tool: str, approve: bool | None) -> tuple[str | None, list[str]]:
    """Run `before_tool` for `tool`, answering a request (if one comes) with `approve`; the result and what was emitted."""
    emitted = Emitted()
    gate = asyncio.create_task(addon.before_tool(tool_name=tool, arguments={"ref": 3, "target": "Buy"}, emit=emitted))
    for _ in range(100):
        if broker.pending or gate.done():
            break
        await asyncio.sleep(0.01)
    if broker.pending:
        assert approve is not None, f"{tool} asked for approval"
        broker.resolve(next(iter(broker.pending)), approve)
    return await gate, [kind for kind, _ in emitted.events]


async def test_site_rules_let_actions_through_or_make_them_ask(chrome: FakeChrome) -> None:
    rules = DomainRules.parse("shop.test=allow,news.test=ask,bank.test=deny")
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard(), rules=rules)
    session = await sessions.open("thread-rules")
    broker = ApprovalBroker()
    risky = frozenset({"browser_click", "vault_write"})
    addon = ApprovalAddon(broker, "run-1", risky, policy=BrowserApprovalPolicy(sessions, "thread-rules"))

    # An allowed site: the click goes ahead without asking; other tools still follow the list.
    await session.patch({"url": "https://www.shop.test/cart"})
    assert await approval_for(addon, broker, "browser_click", None) == (None, [])
    denied, kinds = await approval_for(addon, broker, "vault_write", False)
    assert "did not approve" in str(denied) and kinds == [APPROVAL_REQUIRED, APPROVAL_RESOLVED]

    # An `ask` site: even a key press (not on the list here) waits for the user.
    await session.patch({"url": "https://news.test/"})
    assert await approval_for(addon, broker, "browser_press", True) == (None, [APPROVAL_REQUIRED, APPROVAL_RESOLVED])

    # A denied site: no prompt (the tool itself refuses). A site without a rule: the list decides.
    await session.patch({"url": "https://bank.test/"})
    assert await approval_for(addon, broker, "browser_click", None) == (None, [])
    await session.patch({"url": "https://other.test/"})
    assert (await approval_for(addon, broker, "browser_press", None))[1] == []
    assert (await approval_for(addon, broker, "browser_click", True))[1] == [APPROVAL_REQUIRED, APPROVAL_RESOLVED]

    # A thread without a browser, and reading tools, are left to the list.
    other = ApprovalAddon(broker, "run-2", risky, policy=BrowserApprovalPolicy(sessions, "thread-none"))
    assert (await approval_for(other, broker, "browser_click", True))[1] == [APPROVAL_REQUIRED, APPROVAL_RESOLVED]
    assert await approval_for(addon, broker, "browser_read", None) == (None, [])
    await sessions.close_all()


def test_an_allowed_site_skips_the_approval_prompt_in_a_real_run(grok: FakeGrok, chrome: FakeChrome) -> None:
    agent = ScriptedAgent([open_step(), Step(tool="browser_click", arguments=json.dumps({"ref": 3, "target": "Buy now"})), Step(text="Done.")])
    with browser_client(grok, agent, chrome, rules=DomainRules.parse("shop.test=allow")) as client, client.websocket_connect("/ws") as ws:
        start_agent(ws, grok, "buy boots")
        events = until_run(ws, "completed")
        assert "approval.request" not in {event["type"] for event in events}
        assert [p["type"] for p in chrome.params("Input.dispatchMouseEvent")] == ["mousePressed", "mouseReleased"]
        assert any(e["type"] == "widget.update" and e["patch"].get("rule") == "allow" for e in events)


# ------------------------------------------------------------ taking over


def again(grok: FakeGrok, run_id: str, call_id: str, response_id: str) -> str:
    """The voice model, told run `run_id` is done, starts another agent run on the thread; its id."""
    told(grok, f"[agent {run_id}] done")
    say(grok, "Carry on.")
    call(grok, "run_agent", {"task": "carry on"}, call_id, response_id)
    started = tool_output(grok)
    assert started["status"] == "running"
    return str(started["run_id"])


def control_patch(ws: Any, widget_id: str, control: str) -> None:
    """Read events until the browser widget says who drives it now."""
    while True:
        event = next_event(ws, "widget.update")
        if event["id"] == widget_id and event["patch"].get("control") == control:
            return


def test_the_user_takes_the_browser_over_and_the_agent_stops_until_they_hand_it_back(grok: FakeGrok, chrome: FakeChrome) -> None:
    read = Step(tool="browser_read")
    agent = ScriptedAgent([open_step(), Step(text="opened"), read, Step(text="waiting"), read, Step(text="read")])
    with browser_client(grok, agent, chrome) as client, client.websocket_connect("/ws") as ws:
        first = start_agent(ws, grok, "log in to the shop")
        widget_id = next_event(ws, "widget.render")["widget"]["id"]
        until_run(ws, "completed")

        user_sends(ws, type="browser.takeover", id="w-other", user=True)  # not this thread's browser: ignored
        user_sends(ws, type="browser.takeover", id=widget_id, user=True)
        control_patch(ws, widget_id, "user")
        # The user's own input still reaches the page while they drive.
        user_sends(ws, type="browser.key", id=widget_id, key="a", code="KeyA", keyCode=65)
        until(lambda: len(chrome.params("Input.dispatchKeyEvent")) == 2)

        second = again(grok, first, "a2", "r2")
        until_run(ws, "completed")
        assert chrome.params("Accessibility.getFullAXTree") == []  # the agent's read was refused

        user_sends(ws, type="browser.takeover", id=widget_id, user=False)
        control_patch(ws, widget_id, "agent")
        again(grok, second, "a3", "r3")
        until_run(ws, "completed")
        assert len(chrome.params("Accessibility.getFullAXTree")) == 1


async def test_take_over_marks_the_widget_and_refuses_agent_tools(chrome: FakeChrome) -> None:
    sessions, shown = sessions_for(chrome)
    RUN_BROWSER.set("thread-take")
    await use(sessions, "browser_open", url=PAGE)
    await sessions.take_over("thread-take", True)
    assert {"control": "user"} in shown.updates()
    refused = await use(sessions, "browser_click", ref=3, target="Buy now")
    assert "taken over" in refused["error"] and chrome.params("Input.dispatchMouseEvent") == []
    await sessions.take_over("thread-take", False)
    assert {"control": "agent"} in shown.updates()
    assert await use(sessions, "browser_click", ref=3, target="Buy now") == {"clicked": "Buy now"}
    await sessions.take_over("thread-none", True)  # no browser there: nothing happens
    await sessions.close_all()


def test_settings_carry_the_rules(tmp_path: Path) -> None:
    settings = Settings(browser_rules=DomainRules.parse("a.test=deny"), browser_profile=tmp_path, browser_visible=True)
    assert settings.browser_rules.decide("https://a.test") == "deny"


def test_rules_match_a_trailing_dot_and_an_internationalised_name() -> None:
    rules = DomainRules.parse("bank.example=deny,bücher.de=deny,Shop.Test.=allow")
    assert rules.decide("https://bank.example./login") == "deny"
    assert rules.decide("https://BANK.EXAMPLE../") == "deny"
    assert rules.decide("https://bücher.de/") == "deny"
    assert rules.decide("https://xn--bcher-kva.de/") == "deny"
    assert rules.decide("https://www.BÜCHER.de./") == "deny"
    assert rules.decide("https://shop.test/") == "allow"


async def test_a_denied_site_spelt_another_way_is_not_opened(chrome: FakeChrome) -> None:
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard(), rules=DomainRules.parse("bank.example=deny,bücher.de=deny"))
    RUN_BROWSER.set("thread-spelling")
    for url in ("https://bank.example./", "https://www.xn--bcher-kva.de/", "https://BÜCHER.de/"):
        assert "blocked" in (await use(sessions, "browser_open", url=url))["error"]
    assert chrome.params("Page.navigate") == []
    await sessions.close_all()

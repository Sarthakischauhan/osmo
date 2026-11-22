"""The agent browser stays off this machine and its network (B2), compares hosts in one form (S2), and acts only on
the page an approval was decided for (S3); `allow` stops applying where the user browsed themselves (S7).
The host spelling rules (S2) are in test_browser_upgrades."""

from __future__ import annotations

import pytest
from test_browser import PAGE, FakeChrome, chrome, use

from symphony_server.browser_guard import NetworkGuard
from symphony_server.browser_rules import DomainRules
from symphony_server.browser_sessions import RUN_BROWSER, BrowserSessions
from symphony_server.browser_tools import BrowserApprovalPolicy
from symphony_server.canvas import WidgetBoard

__all__ = ["chrome"]  # the fixture, shared with test_browser

OWN_TOKEN_URL = "http://127.0.0.1:8787/api/music/spotify/token"


async def public(host: str) -> list[str]:
    return {"rebind.test": ["93.184.215.14", "127.0.0.1"], "metadata.test": ["169.254.169.254"]}.get(host, ["93.184.215.14"])


async def unresolvable(host: str) -> list[str]:
    raise OSError(f"no such host {host}")


@pytest.mark.parametrize(
    "url",
    [
        OWN_TOKEN_URL,
        "http://127.8.9.10/",
        "http://[::1]:8787/",
        "http://[::ffff:127.0.0.1]/",
        "http://10.0.0.5/admin",
        "http://172.16.3.4/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/",
        "http://[fd00::5]/",
        "http://100.64.0.1/",
        "http://0.0.0.0:8787/",
        "http://localhost:8787/",
        "http://LOCALHOST.:8787/",
        "http://osmo.localhost/",
        "http://printer.local/",
        "ws://127.0.0.1:8787/ws",
        "http://rebind.test/",
        "http://metadata.test/",
    ],
)
async def test_the_guard_refuses_this_machine_and_its_network(url: str) -> None:
    assert await NetworkGuard(public).refusal(url) is not None


@pytest.mark.parametrize("url", ["https://shop.test/boots", "http://93.184.215.14/", "about:blank", "data:text/html,hi", "blob:https://shop.test/1"])
async def test_the_guard_lets_the_public_internet_through(url: str) -> None:
    assert await NetworkGuard(public).refusal(url) is None


async def test_a_name_that_does_not_resolve_is_refused() -> None:
    assert await NetworkGuard(unresolvable).refusal("https://nowhere.test/") == "nowhere.test does not resolve"


async def test_browser_open_refuses_osmos_own_api_without_navigating(chrome: FakeChrome) -> None:
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard())
    RUN_BROWSER.set("thread-own-api")
    for url in (OWN_TOKEN_URL, "http://localhost:8787/api/vault/notes", "http://[::1]:8787/api/calendar/export.ics"):
        assert "public internet" in (await use(sessions, "browser_open", url=url))["error"]
    assert chrome.params("Page.navigate") == []
    await sessions.close_all()


async def test_a_name_resolving_to_loopback_is_refused_before_navigating(chrome: FakeChrome) -> None:
    chrome.dns["sneaky.test"] = ["127.0.0.1"]
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard())
    RUN_BROWSER.set("thread-sneaky")
    assert "private or local address" in (await use(sessions, "browser_open", url="http://sneaky.test:8787/api/music/spotify/token"))["error"]
    assert chrome.params("Page.navigate") == []
    await sessions.close_all()


async def test_a_redirect_hop_to_a_private_host_is_failed_before_chrome_requests_it(chrome: FakeChrome) -> None:
    chrome.redirects[PAGE] = "http://169.254.169.254/latest/meta-data/"
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard())
    RUN_BROWSER.set("thread-redirect")
    error = (await use(sessions, "browser_open", url=PAGE))["error"]
    assert "169.254.169.254 is a private or local address" in error
    failed = chrome.params("Fetch.failRequest")
    assert len(failed) == 1 and failed[0]["errorReason"] == "BlockedByClient"
    await sessions.close_all()


async def test_every_request_of_the_browser_is_screened(chrome: FakeChrome) -> None:
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard())
    session = await sessions.open("thread-subresources")
    assert chrome.params("Fetch.enable") == [{"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}]
    assert await chrome.request("http://192.168.1.1/admin", "XHR") == "Fetch.failRequest"
    assert await chrome.request("http://localhost:8787/api/music/spotify/token", "Image") == "Fetch.failRequest"
    assert await chrome.request("https://cdn.test/boots.png", "Image") == "Fetch.continueRequest"
    assert session.refused is None  # subresources do not stand for the page
    await sessions.close_all()


async def test_an_action_refuses_when_the_page_moved_after_its_approval(chrome: FakeChrome) -> None:
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard(), rules=DomainRules.parse("shop.test=allow"))
    RUN_BROWSER.set("thread-moved")
    session = await sessions.open("thread-moved")
    await session.patch({"url": PAGE})
    assert BrowserApprovalPolicy(sessions, "thread-moved").decide("browser_click", {}) == "allow"
    # A meta refresh takes the page elsewhere before the click is dispatched.
    chrome.answers["Runtime.evaluate"] = {"result": {"value": {"url": "https://elsewhere.test/pay", "title": "", "text": ""}}}
    assert "moved to https://elsewhere.test/pay" in (await use(sessions, "browser_click", ref=3, target="Buy now"))["error"]
    assert chrome.params("Input.dispatchMouseEvent") == []
    await sessions.close_all()


async def test_going_back_onto_a_denied_site_leaves_it(chrome: FakeChrome) -> None:
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard(), rules=DomainRules.parse("bank.test=deny"))
    RUN_BROWSER.set("thread-back")
    chrome.answers["Page.getNavigationHistory"] = {"currentIndex": 1, "entries": [{"id": 1, "url": "https://bank.test/"}, {"id": 2, "url": PAGE}]}
    # The bank comes back from the back/forward cache: no request, so nothing for the interception to refuse.
    chrome.answers["Runtime.evaluate"] = lambda params: {
        "result": {"value": {"url": "https://bank.test/" if chrome.params("Page.navigateToHistoryEntry") else PAGE, "title": "", "text": ""}}
    }
    assert "bank.test" in (await use(sessions, "browser_back"))["error"]
    assert [p["url"] for p in chrome.params("Page.navigate")] == ["about:blank"]
    await sessions.close_all()


async def test_allow_stops_applying_on_a_site_the_user_browsed_while_in_control(chrome: FakeChrome) -> None:
    sessions = BrowserSessions(str(chrome.executable), WidgetBoard(), rules=DomainRules.parse("shop.test=allow"))
    session = await sessions.open("thread-user")
    await session.patch({"url": PAGE})
    policy = BrowserApprovalPolicy(sessions, "thread-user")
    assert policy.decide("browser_click", {}) == "allow"
    await sessions.take_over("thread-user", True)
    await sessions.take_over("thread-user", False)
    assert policy.decide("browser_click", {}) == "ask"
    await sessions.close_all()


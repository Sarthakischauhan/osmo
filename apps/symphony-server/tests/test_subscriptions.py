"""Subscription sign-in (SuperGrok, ChatGPT, Claude) through core_ai's OAuth, kept in Osmo's SecretStore."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from core_ai import ModelRegistry
from core_ai.oauth import OAuthToken
from core_ai.oauth.xai import CLI_CHAT_PROXY_BASE_URL, DEVICE_CODE_URL, TOKEN_URL
from core_ai.types import Message
from pydantic import SecretStr

from conftest import make_client
from symphony_server.file_store import FileSecretStore
from symphony_server.model_access import ModelAccess
from symphony_server.provider_router import ProviderNotConnected
from symphony_server.providers import CredentialStore
from symphony_server.secret_store import DEFAULT_OWNER, ProviderAuth
from symphony_server.settings import Settings
from symphony_server.subscriptions import SubscriptionRefresher, SubscriptionStore, subscription_provider

SETUP_TOKEN = "sk-ant-oat01-" + "x" * 90


def settings(tmp_path: Path) -> Settings:
    return Settings(xai_base_url="http://127.0.0.1:9/v1", credentials_file=tmp_path / "creds.enc", subscription_loopback=False)


class FakeXai:
    """auth.x.ai's device flow and token refresh."""

    def __init__(self, pending: int = 1) -> None:
        self.pending = pending
        self.refreshes = 0
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if str(request.url) == DEVICE_CODE_URL:
            assert "grok-cli:access" in form["scope"]
            return httpx.Response(
                200,
                json={"device_code": "dev-1", "user_code": "ABCD-EFGH", "verification_uri": "https://accounts.x.ai/device", "interval": 1},
            )
        assert str(request.url) == TOKEN_URL
        if form["grant_type"] == "refresh_token":
            with self.lock:
                self.refreshes += 1
                n = self.refreshes
            time.sleep(0.05)
            return httpx.Response(200, json={"access_token": f"xat-{n + 1}", "refresh_token": f"xrt-{n + 1}", "expires_in": 3600})
        if self.pending:
            self.pending -= 1
            return httpx.Response(400, json={"error": "authorization_pending"})
        return httpx.Response(200, json={"access_token": "xat-1", "refresh_token": "xrt-1", "expires_in": 3600})


def wait_for(client, path: str, status: str, timeout: float = 10.0) -> dict:  # type: ignore[no-untyped-def]
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(path).json()
        if body["status"] == status:
            return body
        time.sleep(0.1)
    raise AssertionError(f"{path} never became {status}: {body}")


def providers(client) -> dict[str, dict]:  # type: ignore[no-untyped-def]
    return {p["id"]: p for p in client.get("/providers").json()["providers"]}


def test_grok_device_sign_in_stores_the_token_beside_the_api_key(tmp_path: Path) -> None:
    store = FileSecretStore(None)
    store.put(DEFAULT_OWNER, ProviderAuth(provider="xai", kind="api_key", secret=SecretStr("xai-key")))
    with make_client(settings(tmp_path), FakeXai(), secret_store=store) as client:
        listed = providers(client)["xai"]
        assert listed["subscription"] == {"label": "Sign in with Grok", "connected": False, "capabilities": ["Background agents"]}
        started = client.post("/providers/xai/subscription/login").json()
        assert started["kind"] == "device" and started["userCode"] == "ABCD-EFGH"
        assert started["url"] == "https://accounts.x.ai/device/ABCD-EFGH"
        wait_for(client, f"/providers/xai/subscription/login/{started['loginId']}", "connected")

        listed = providers(client)["xai"]
        assert listed["connected"] is True and listed["subscription"]["connected"] is True
        assert "xat-1" not in client.get("/providers").text
        # The API key is untouched: voice still uses it.
        assert store.get(DEFAULT_OWNER, "xai").secret.get_secret_value() == "xai-key"  # type: ignore[union-attr]
        saved = store.get(DEFAULT_OWNER, "xai-subscription")
        assert saved is not None and json.loads(saved.secret.get_secret_value())["refresh_token"] == "xrt-1"

        assert client.delete("/providers/xai/subscription").status_code == 204
        assert providers(client)["xai"]["subscription"]["connected"] is False
        assert store.get(DEFAULT_OWNER, "xai") is not None


def test_subscription_alone_unlocks_text_but_not_voice(tmp_path: Path) -> None:
    with make_client(settings(tmp_path), FakeXai(pending=0)) as client:
        started = client.post("/providers/xai/subscription/login").json()
        wait_for(client, f"/providers/xai/subscription/login/{started['loginId']}", "connected")
        listed = providers(client)["xai"]
        assert listed["connected"] is False and listed["subscription"]["connected"] is True
        xai_models = [m for m in client.get("/voice-models").json()["models"] if m["provider"] == "xai"]
        assert xai_models and all(m["locked"] for m in xai_models)


def test_claude_setup_token_paste(tmp_path: Path) -> None:
    with make_client(settings(tmp_path)) as client:
        started = client.post("/providers/anthropic/subscription/login").json()
        assert started["kind"] == "paste" and started["url"].startswith("https://claude.ai/oauth/authorize")
        login = f"/providers/anthropic/subscription/login/{started['loginId']}"
        refused = client.post(f"{login}/paste", json={"value": "sk-ant-api03-not-a-subscription"})
        assert refused.status_code == 400
        assert client.get(login).json()["status"] == "pending"
        done = client.post(f"{login}/paste", json={"value": SETUP_TOKEN})
        assert done.json() == {"status": "connected", "error": ""}
        assert providers(client)["anthropic"]["subscription"]["connected"] is True
        assert SETUP_TOKEN not in client.get("/providers").text


def test_a_cancelled_sign_in_reports_it_and_a_new_one_replaces_the_old(tmp_path: Path) -> None:
    with make_client(settings(tmp_path)) as client:
        first = client.post("/providers/openai/subscription/login").json()
        assert first["kind"] == "browser" and "auth.openai.com" in first["url"]
        second = client.post("/providers/openai/subscription/login").json()
        assert client.get(f"/providers/openai/subscription/login/{first['loginId']}").status_code == 404
        assert client.delete(f"/providers/openai/subscription/login/{second['loginId']}").status_code == 204
        wait_for(client, f"/providers/openai/subscription/login/{second['loginId']}", "cancelled")


def test_disconnect_cancels_pending_login(tmp_path: Path) -> None:
    with make_client(settings(tmp_path), FakeXai(pending=100)) as client:
        started = client.post("/providers/xai/subscription/login").json()
        assert client.delete("/providers/xai/subscription").status_code == 204
        assert client.get(f"/providers/xai/subscription/login/{started['loginId']}").json()["status"] == "cancelled"
        assert not providers(client)["xai"]["subscription"]["connected"]


def test_providers_without_a_subscription_refuse_sign_in(tmp_path: Path) -> None:
    with make_client(settings(tmp_path)) as client:
        assert providers(client)["gemini"]["subscription"] is None
        assert client.post("/providers/gemini/subscription/login").status_code == 404


# ------------------------------------------------------------------ requests and refresh


def subscription_store(token: OAuthToken, provider: str = "xai") -> SubscriptionStore:
    store = SubscriptionStore(CredentialStore(FileSecretStore(None)))
    store.save(provider, token)
    return store


SSE = 'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'


async def test_grok_subscription_calls_the_cli_proxy_and_renews_a_rejected_token() -> None:
    fake = FakeXai()
    store = subscription_store(OAuthToken(access_token="xat-1", refresh_token="xrt-1", expires_at=time.time() + 3600))
    refresher = SubscriptionRefresher(store, httpx.Client(transport=httpx.MockTransport(fake)))
    seen: list[httpx.Request] = []

    def proxy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers["authorization"] == "Bearer xat-1":
            return httpx.Response(401, json={"error": {"message": "no auth context"}})
        return httpx.Response(200, text=SSE, headers={"content-type": "text/event-stream"})

    provider = subscription_provider("xai", store.token("xai"), refresher, transport=httpx.MockTransport(proxy))  # type: ignore[arg-type]
    events = [event async for event in provider.stream("grok-4.7", [Message(role="user", content="hello")])]
    assert [e.delta for e in events if e.type == "text_delta"] == ["hi"]
    assert str(seen[0].url) == f"{CLI_CHAT_PROXY_BASE_URL}/chat/completions"
    assert seen[0].headers["x-xai-token-auth"] == "xai-grok-cli"
    assert seen[-1].headers["authorization"] == "Bearer xat-2"
    assert fake.refreshes == 1
    assert store.token("xai").refresh_token == "xrt-2"  # type: ignore[union-attr]


def test_concurrent_rejections_spend_the_refresh_token_once() -> None:
    fake = FakeXai()
    store = subscription_store(OAuthToken(access_token="xat-1", refresh_token="xrt-1", expires_at=time.time() + 3600))
    refresher = SubscriptionRefresher(store, httpx.Client(transport=httpx.MockTransport(fake)))
    results: list[str] = []
    threads = [
        threading.Thread(target=lambda: results.append(refresher.current("xai", rejected="xat-1").access_token))  # type: ignore[union-attr]
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert fake.refreshes == 1 and results == ["xat-2"] * 4


def test_an_expiring_token_is_refreshed_and_an_expired_one_without_refresh_is_dropped() -> None:
    fake = FakeXai()
    store = subscription_store(OAuthToken(access_token="xat-1", refresh_token="xrt-1", expires_at=time.time() + 30))
    refresher = SubscriptionRefresher(store, httpx.Client(transport=httpx.MockTransport(fake)))
    assert refresher.current("xai").access_token == "xat-2"  # type: ignore[union-attr]
    store.save("xai", OAuthToken(access_token="old", expires_at=time.time() - 10))
    assert refresher.current("xai") is None


def test_text_prefers_the_subscription_and_images_and_voice_keep_the_key() -> None:
    credentials = CredentialStore(FileSecretStore(None))
    subscriptions = SubscriptionRefresher(SubscriptionStore(credentials))
    access = ModelAccess(ModelRegistry(), credentials, "https://api.x.ai/v1", subscriptions=subscriptions)
    router = access.routers["grok"]

    subscriptions.store.save("xai", OAuthToken(access_token="xat-1"))
    assert access.connect("xai") is True and access.voice_ready("xai") is False
    assert router.text is not None and router.text.base_url == CLI_CHAT_PROXY_BASE_URL  # type: ignore[attr-defined]
    with pytest.raises(ProviderNotConnected, match="API key"):
        import asyncio

        asyncio.run(router.generate_image("grok-imagine-image-2.0", "a cat"))

    credentials.save(ProviderAuth(provider="xai", kind="api_key", secret=SecretStr("xai-key")))
    assert access.voice_ready("xai") is True
    assert router.text.base_url == CLI_CHAT_PROXY_BASE_URL  # type: ignore[attr-defined, union-attr]
    assert router.images is not None and router.images.api_key == "xai-key"  # type: ignore[attr-defined]

    subscriptions.store.remove("xai")
    assert access.connect("xai") is True and router.text is router.images


async def test_agent_automatically_selects_subscription_and_respects_override() -> None:
    from typing import Any, cast
    from core_ai.providers.defaults import DEFAULT_MODELS
    from symphony_server.agents import AgentRuns
    from symphony_server.browser import BrowserSessions
    from symphony_server.canvas import WidgetBoard
    from symphony_server.storage import SQLiteStorage

    credentials = CredentialStore(FileSecretStore(None))
    subscriptions = SubscriptionRefresher(SubscriptionStore(credentials))
    access = ModelAccess(ModelRegistry(), credentials, "https://api.x.ai/v1", subscriptions=subscriptions)
    subscriptions.store.save("openai", OAuthToken(access_token="token", account_id="account"))
    runs = AgentRuns(cast(Any, None), SQLiteStorage(None), cast(Any, None), access, "grok:custom", BrowserSessions(None, WidgetBoard()), auto_model=True)
    assert await runs.select_model() == DEFAULT_MODELS["openai"]
    runs.auto_model = False
    assert await runs.select_model() == "grok:custom"
    runs.auto_model = True
    subscriptions.store.save("openai", OAuthToken(access_token="expired", expires_at=time.time() - 10))
    assert await runs.select_model() == "grok:custom"
    credentials.save(ProviderAuth(provider="gemini", kind="api_key", secret=SecretStr("key")))
    assert await runs.select_model() == DEFAULT_MODELS["gemini"]


async def test_expired_subscription_uses_key_or_reports_disconnected() -> None:
    credentials = CredentialStore(FileSecretStore(None))
    subscriptions = SubscriptionRefresher(SubscriptionStore(credentials))
    access = ModelAccess(ModelRegistry(), credentials, "https://api.x.ai/v1", subscriptions=subscriptions)
    subscriptions.store.save("xai", OAuthToken(access_token="expired", expires_at=time.time() - 10))
    assert not await access.prepare("xai")
    credentials.save(ProviderAuth(provider="xai", kind="api_key", secret=SecretStr("key")))
    assert await access.prepare("xai")
    assert access.routers["grok"].text is access.routers["grok"].images


def test_file_store_serializes_concurrent_provider_writes(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    store = FileSecretStore(path)
    errors: list[Exception] = []

    def write(provider: str) -> None:
        try:
            for i in range(30):
                store.put(DEFAULT_OWNER, ProviderAuth(provider=provider, kind="oauth", secret=SecretStr(str(i))))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(p,)) for p in ("xai-subscription", "openai-subscription")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    reopened = FileSecretStore(path)
    assert reopened.list_providers(DEFAULT_OWNER) == ["openai-subscription", "xai-subscription"]


def test_chatgpt_subscription_runs_on_codex_with_the_account_header() -> None:
    store = subscription_store(OAuthToken(access_token="oat", account_id="acct-1"), provider="openai")
    provider = subscription_provider("openai", store.token("openai"), SubscriptionRefresher(store))  # type: ignore[arg-type]
    assert provider.base_url == "https://chatgpt.com/backend-api/codex"  # type: ignore[attr-defined]
    assert provider.extra_headers["ChatGPT-Account-Id"] == "acct-1"  # type: ignore[attr-defined]


def test_claude_subscription_sends_a_bearer_with_the_oauth_beta() -> None:
    store = subscription_store(OAuthToken(access_token=SETUP_TOKEN), provider="anthropic")
    provider = subscription_provider("anthropic", store.token("anthropic"), SubscriptionRefresher(store))  # type: ignore[arg-type]
    headers = provider._headers  # type: ignore[attr-defined]
    assert headers["Authorization"] == f"Bearer {SETUP_TOKEN}" and "x-api-key" not in headers
    assert headers["anthropic-beta"] == "oauth-2025-04-20"


async def test_a_voice_only_xai_key_does_not_run_agents_and_a_text_key_keeps_the_configured_model() -> None:
    from typing import Any, cast
    from core_ai.providers.defaults import DEFAULT_MODELS
    from symphony_server.agents import AgentRuns
    from symphony_server.browser import BrowserSessions
    from symphony_server.canvas import WidgetBoard
    from symphony_server.storage import SQLiteStorage

    listed: list[dict[str, Any]] = []
    calls: list[str] = []

    def xai(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert str(request.url) == "https://api.x.ai/v1/language-models"
        return httpx.Response(200, json={"models": listed})

    credentials = CredentialStore(FileSecretStore(None))
    credentials.save(ProviderAuth(provider="xai", kind="api_key", secret=SecretStr("voice-only")))
    credentials.save(ProviderAuth(provider="gemini", kind="api_key", secret=SecretStr("gemini-key")))
    access = ModelAccess(
        ModelRegistry(), credentials, "https://api.x.ai/v1",
        subscriptions=SubscriptionRefresher(SubscriptionStore(credentials)),
        http=httpx.AsyncClient(transport=httpx.MockTransport(xai)),
    )
    runs = AgentRuns(cast(Any, None), SQLiteStorage(None), cast(Any, None), access, "grok:grok-4.20-0309-reasoning", BrowserSessions(None, WidgetBoard()), auto_model=True)
    # The team lists no language models (its key serves voice only): Gemini runs the agent.
    assert await runs.select_model() == DEFAULT_MODELS["gemini"]
    assert await runs.select_model() == DEFAULT_MODELS["gemini"] and len(calls) == 1  # cached per key

    # A key that can run the configured model keeps it rather than core_ai's default.
    listed.append({"id": "grok-4.20-0309-reasoning", "aliases": []})
    credentials.save(ProviderAuth(provider="xai", kind="api_key", secret=SecretStr("text-key")))
    assert await runs.select_model() == "grok:grok-4.20-0309-reasoning"

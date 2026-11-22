"""Subscription sign-in: core_ai's OAuth logins (SuperGrok, ChatGPT, Claude) kept in Osmo's SecretStore.

core_ai (`core_ai.oauth`) runs each provider's login and knows the endpoint and headers a
subscription token is used with; its own token file (`~/.symphony/oauth/`) is not used here.
Tokens go to the SecretStore under `<provider>-subscription`, next to (never replacing) the
provider's API key, and never leave the server.

A subscription serves text only (background agents, desktop vision). core_ai's voice sessions
take a plain API key, and no provider documents its realtime or image APIs for these tokens, so
voice and images keep needing the API key.

- xAI: device-code login (`auth.x.ai`, the Grok CLI client); requests go to the Grok CLI proxy.
- OpenAI: "Sign in with ChatGPT" (Codex client, PKCE). The redirect goes to `localhost:1455`, which the
  server listens on while a login is pending; when it runs elsewhere the user pastes the redirect URL.
- Anthropic: paste a `claude setup-token` token, or the `code#state` from Claude's sign-in page.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import httpx
from core_ai import AnthropicProvider, GrokProvider, OpenAIProvider
from core_ai.oauth import LoginCancelled, LoginFlow, OAuthToken, start_login
from core_ai.oauth.anthropic import OAUTH_BETA
from core_ai.oauth.openai_codex import CODEX_BASE_URL, codex_request_headers
from core_ai.oauth.store import refresh_stored_token
from core_ai.oauth.xai import CLI_CHAT_PROXY_BASE_URL, grok_cli_request_headers
from core_ai.providers.base import BaseProvider
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from symphony_server.secret_store import ProviderAuth

if TYPE_CHECKING:
    from symphony_server.providers import CredentialStore

log = logging.getLogger("symphony.subscriptions")

# A login must finish within this many seconds (core_ai's device codes last about as long).
LOGIN_TIMEOUT_S = 600.0
# A subscription token is refreshed when it has less than this many seconds left.
REFRESH_MARGIN_S = 120.0
# How long a paste waits for the login to store its token before answering.
PASTE_SETTLE_S = 5.0
ERROR_MAX = 200

LoginKind = Literal["browser", "device", "paste"]
LoginStatus = Literal["pending", "connected", "failed", "cancelled"]


@dataclass(frozen=True)
class SubscriptionSpec:
    # core_ai's OAuth provider id (`core_ai.oauth.supports_oauth`).
    core_id: str
    label: str
    capabilities: tuple[str, ...] = ("Background agents",)


SUBSCRIPTIONS: dict[str, SubscriptionSpec] = {
    "xai": SubscriptionSpec(core_id="grok", label="Sign in with Grok"),
    "openai": SubscriptionSpec(core_id="openai", label="Sign in with ChatGPT"),
    "anthropic": SubscriptionSpec(core_id="anthropic", label="Sign in with Claude"),
}


def subscription_id(provider: str) -> str:
    """The SecretStore (and connection row) id of `provider`'s subscription credential."""
    return f"{provider}-subscription"


class SubscriptionStore:
    """The subscription half of a `CredentialStore`: core_ai `OAuthToken`s as JSON secrets."""

    def __init__(self, credentials: CredentialStore) -> None:
        self.credentials = credentials

    def token(self, provider: str) -> OAuthToken | None:
        if provider not in SUBSCRIPTIONS:
            return None
        stored = self.credentials.stored(subscription_id(provider))
        if stored is None:
            return None
        try:
            payload = json.loads(stored.secret.get_secret_value())
        except ValueError:
            log.warning("the stored %s subscription is not valid JSON; sign in again", provider)
            return None
        token = OAuthToken.from_json(payload) if isinstance(payload, dict) else None
        return token if token is not None and token.access_token else None

    def save(self, provider: str, token: OAuthToken) -> None:
        secret = SecretStr(json.dumps(token.to_json()))
        self.credentials.save(ProviderAuth(provider=subscription_id(provider), kind="oauth", secret=secret))

    def remove(self, provider: str) -> None:
        self.credentials.remove(subscription_id(provider))


class SubscriptionRefresher:
    """Refreshes subscription tokens, one refresh per provider at a time.

    Refresh tokens rotate, so two runs must never spend the same one: refreshes take the provider's
    lock and re-read the stored token first. Sync (core_ai's refresh calls and its providers'
    `credential_refresher` hook are), so async callers run it in a thread."""

    def __init__(self, store: SubscriptionStore, client: httpx.Client | None = None) -> None:
        self.store, self.client = store, client
        self.locks: dict[str, threading.Lock] = {p: threading.Lock() for p in SUBSCRIPTIONS}

    def current(self, provider: str, *, rejected: str | None = None) -> OAuthToken | None:
        """The provider's usable token: refreshed when it is about to expire, or when the provider
        rejected `rejected` (an access token) and no other refresh has replaced it yet."""
        lock = self.locks.get(provider)
        if lock is None:
            return None
        with lock:
            token = self.store.token(provider)
            if token is None:
                return None
            if rejected is not None and token.access_token != rejected:
                return token
            if rejected is None and not token.is_expired(skew=REFRESH_MARGIN_S):
                return token
            if not token.refresh_token:
                return None if token.is_expired() or rejected is not None else token
            try:
                refreshed = refresh_stored_token(SUBSCRIPTIONS[provider].core_id, token, client=self.client)
            except Exception as exc:  # noqa: BLE001 - any refresh failure means "sign in again"
                log.warning("could not refresh the %s subscription (%s); sign in again", provider, type(exc).__name__)
                return None
            self.store.save(provider, refreshed)
            log.info("%s subscription token refreshed", provider)
            return refreshed


def request_config(provider: str, token: OAuthToken) -> dict[str, Any]:
    """The bearer, base URL and headers core_ai's provider sends a subscription token with
    (the shape `credential_refresher` returns)."""
    if provider == "xai":
        return {"api_key": token.access_token, "base_url": CLI_CHAT_PROXY_BASE_URL, "extra_headers": grok_cli_request_headers()}
    if provider == "openai":
        return {"api_key": token.access_token, "base_url": CODEX_BASE_URL, "extra_headers": codex_request_headers(token)}
    if provider == "anthropic":
        return {"api_key": token.access_token, "base_url": "", "extra_headers": {"anthropic-beta": OAUTH_BETA}}
    raise KeyError(provider)


def subscription_provider(
    provider: str, token: OAuthToken, refresher: SubscriptionRefresher, transport: httpx.AsyncBaseTransport | None = None
) -> BaseProvider:
    """core_ai's text provider on a subscription token. When the provider rejects the token, it asks
    `refresher` for a replacement once and retries (core_ai's `credential_refresher` hook)."""

    def refresh(current: Any) -> dict[str, Any] | None:
        replacement = refresher.current(provider, rejected=str(getattr(current, "api_key", "")))
        return request_config(provider, replacement) if replacement is not None else None

    config = request_config(provider, token)
    if provider == "anthropic":
        return AnthropicProvider(
            config["api_key"], transport=transport, extra_headers=config["extra_headers"], use_bearer=True, credential_refresher=refresh
        )
    factory = GrokProvider if provider == "xai" else OpenAIProvider
    return factory(
        config["api_key"], base_url=config["base_url"], transport=transport, extra_headers=config["extra_headers"], credential_refresher=refresh
    )


# ------------------------------------------------------------------ sign-in


@dataclass
class Login:
    id: str
    provider: str
    flow: LoginFlow
    status: LoginStatus = "pending"
    error: str = ""
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class LoginStarted(BaseModel):
    """What the browser shows while a login is pending. Never carries a token."""

    loginId: str
    kind: LoginKind
    title: str
    instructions: str
    url: str = ""
    userCode: str = ""
    pasteHint: str = ""


class LoginState(BaseModel):
    status: LoginStatus
    error: str = ""


class PasteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: SecretStr = Field(min_length=1)


def safe_error(exc: BaseException) -> str:
    """A login failure for the user. core_ai's messages are provider error descriptions, never tokens."""
    if isinstance(exc, LoginCancelled):
        return "Sign-in cancelled"
    if isinstance(exc, TimeoutError):
        return "Sign-in timed out; start again"
    return (str(exc) or type(exc).__name__)[:ERROR_MAX]


StartLogin = Callable[..., LoginFlow]


class SubscriptionLogins:
    """Pending logins by id; at most one per provider (a new one cancels the old: OpenAI's redirect
    port can serve only one)."""

    def __init__(
        self,
        store: SubscriptionStore,
        on_connected: Callable[[str], None],
        *,
        client: httpx.Client | None = None,
        bind_loopback: bool = True,
        starter: StartLogin = start_login,
    ) -> None:
        self.store, self.on_connected, self.client = store, on_connected, client
        self.bind_loopback, self.starter = bind_loopback, starter
        self.logins: dict[str, Login] = {}
        self.generations: dict[str, int] = {provider: 0 for provider in SUBSCRIPTIONS}

    async def start(self, provider: str) -> Login:
        self.generations[provider] += 1
        generation = self.generations[provider]
        for login in [entry for entry in self.logins.values() if entry.provider == provider]:
            self.cancel(login)
            self.logins.pop(login.id, None)
        spec = SUBSCRIPTIONS[provider]
        # The xAI device-code request is a blocking HTTP call.
        flow = await asyncio.to_thread(
            self.starter, spec.core_id, open_browser=False, bind_loopback=self.bind_loopback, client=self.client
        )
        if self.generations[provider] != generation:
            await asyncio.to_thread(flow.close)
            raise HTTPException(409, "sign-in was cancelled or replaced")
        login = Login(id=secrets.token_urlsafe(16), provider=provider, flow=flow)
        login.task = asyncio.create_task(self.wait(login))
        self.logins[login.id] = login
        return login

    async def wait(self, login: Login) -> None:
        try:
            token = await asyncio.to_thread(login.flow.wait, LOGIN_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - every failure ends the login with a message
            if login.status == "pending":
                login.status = "cancelled" if isinstance(exc, LoginCancelled) else "failed"
                login.error = safe_error(exc)
                log.info("%s sign-in ended: %s", login.provider, login.status)
            return
        # Cancellation or replacement may have happened while the worker returned.
        if login.status != "pending" or self.logins.get(login.id) is not login:
            return
        try:
            self.store.save(login.provider, token)
        except HTTPException as exc:
            login.status, login.error = "failed", str(exc.detail)
            return
        self.on_connected(login.provider)
        login.status = "connected"
        log.info("%s connected with a subscription", login.provider)

    def get(self, provider: str, login_id: str) -> Login:
        login = self.logins.get(login_id)
        if login is None or login.provider != provider:
            raise HTTPException(404, "no such sign-in; start again")
        return login

    async def paste(self, login: Login, value: str) -> None:
        if login.status != "pending":
            raise HTTPException(409, f"this sign-in is {login.status}")
        try:
            await asyncio.to_thread(login.flow.complete_from_paste, value)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, safe_error(exc)) from None
        if login.task is not None:
            await asyncio.wait([login.task], timeout=PASTE_SETTLE_S)

    def cancel(self, login: Login) -> None:
        if login.status == "pending":
            login.status = "cancelled"
            login.error = "Sign-in cancelled"
        login.flow.close()

    def close_all(self) -> None:
        for provider in self.generations:
            self.generations[provider] += 1
        for login in self.logins.values():
            self.cancel(login)


def started(login: Login) -> LoginStarted:
    prompt = login.flow.prompt
    return LoginStarted(
        loginId=login.id,
        kind=prompt.kind,
        title=prompt.title,
        instructions=prompt.instructions,
        url=prompt.url,
        userCode=prompt.user_code,
        pasteHint=prompt.paste_hint,
    )


def state(login: Login) -> LoginState:
    return LoginState(status=login.status, error=login.error)


def create_subscription_router(
    store: SubscriptionStore, logins: SubscriptionLogins, on_disconnected: Callable[[str], None],
    refresher: SubscriptionRefresher | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/providers/{provider_id}/subscription")

    def spec_or_404(provider_id: str) -> SubscriptionSpec:
        spec = SUBSCRIPTIONS.get(provider_id)
        if spec is None:
            raise HTTPException(404, f"{provider_id!r} has no subscription sign-in")
        return spec

    @router.post("/login")
    async def login_start(provider_id: str) -> LoginStarted:
        spec_or_404(provider_id)
        try:
            login = await logins.start(provider_id)
        except (httpx.HTTPError, RuntimeError, OSError) as exc:
            log.warning("could not start the %s sign-in: %s", provider_id, type(exc).__name__)
            raise HTTPException(502, f"could not start the sign-in: {safe_error(exc)}") from None
        return started(login)

    @router.get("/login/{login_id}")
    async def login_state(provider_id: str, login_id: str) -> LoginState:
        spec_or_404(provider_id)
        return state(logins.get(provider_id, login_id))

    @router.post("/login/{login_id}/paste")
    async def login_paste(provider_id: str, login_id: str, body: PasteRequest) -> LoginState:
        spec_or_404(provider_id)
        login = logins.get(provider_id, login_id)
        await logins.paste(login, body.value.get_secret_value())
        return state(login)

    @router.delete("/login/{login_id}", status_code=204)
    async def login_cancel(provider_id: str, login_id: str) -> None:
        spec_or_404(provider_id)
        logins.cancel(logins.get(provider_id, login_id))

    @router.delete("", status_code=204)
    async def disconnect(provider_id: str) -> None:
        spec_or_404(provider_id)
        logins.generations[provider_id] += 1
        for pending in list(logins.logins.values()):
            if pending.provider == provider_id:
                logins.cancel(pending)
        lock = refresher.locks[provider_id] if refresher is not None else None
        if lock is not None:
            await asyncio.to_thread(lock.acquire)
        try:
            store.remove(provider_id)
            on_disconnected(provider_id)
        finally:
            if lock is not None:
                lock.release()
        log.info("%s subscription removed", provider_id)

    return router

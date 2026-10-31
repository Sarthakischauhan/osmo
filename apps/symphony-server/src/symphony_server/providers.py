"""Provider credentials: which providers are connected, and the endpoints that connect them.

Secrets live only on the server, in a `SecretStore` (secret_store.py; `OSMO_SECRET_STORE` picks the
Fernet file or Infisical). They are kept as `SecretStr` in memory and never logged or returned by any
endpoint: `GET /providers` reports only whether a provider is connected.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from symphony_server.secret_store import DEFAULT_OWNER, AuthKind, ProviderAuth, SecretStore, SecretStoreError
from symphony_server.settings import OAuthClient
from symphony_server.storage import Storage
from symphony_server.voices import VoiceModel

log = logging.getLogger("symphony.providers")

# A started OAuth flow must come back within this many seconds.
OAUTH_TTL_S = 600.0
# Pending OAuth flows kept at once; the oldest are dropped first.
OAUTH_PENDING_MAX = 64
VALIDATE_TIMEOUT_S = 10.0
# An OAuth access token is refreshed when it has less than this many seconds left.
REFRESH_MARGIN_S = 120.0

# `key_exchange`: OpenRouter's PKCE flow, whose code exchange returns a long-lived API key.
# `authorization_code`: standard OAuth 2 code + PKCE with a registered client (id and secret from
# the environment), returning an expiring access token and a refresh token.
OAuthStyle = Literal["key_exchange", "authorization_code"]


@dataclass(frozen=True)
class OAuthSpec:
    """An OAuth 2 authorization-code + PKCE (S256) flow."""

    authorize_url: str
    token_url: str
    style: OAuthStyle = "key_exchange"
    scope: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    # One cheap authenticated GET that succeeds only with a valid key.
    validate_url: str
    oauth: OAuthSpec | None = None
    # The header `validate_url` takes the key in, and the prefix before the key.
    key_header: str = "Authorization"
    key_prefix: str = "Bearer "



# Sign-in per provider, checked against each provider's own docs (October 2026):
# - OpenRouter: OAuth PKCE that returns an API key (https://openrouter.ai/docs/use-cases/oauth-pkce).
# - Vercel: "Sign in with Vercel", OAuth 2 code + PKCE with a registered app
#   (https://vercel.com/docs/sign-in-with-vercel/authorization-server-api); its access token
#   authenticates AI Gateway model requests (https://ai-sdk.dev/providers/ai-sdk-providers/ai-gateway,
#   "Vercel Access Token Authentication"). Offered only when OSMO_VERCEL_OAUTH_CLIENT_ID is set.
# - xAI: API key only. The console issues keys; auth.x.ai is documented only for xAI's own
#   Grok Build CLI, with no third-party client registration.
# - OpenAI: API key only. "Sign in with ChatGPT" tokens are documented for Responses API inference
#   (https://developers.openai.com/siwc/token-sharing-open-source), not the Realtime API Osmo talks to.
# - Gemini: API key only. Google OAuth works for the Gemini API (https://ai.google.dev/gemini-api/docs/oauth),
#   but core_ai's Gemini text and Live clients send only `x-goog-api-key` / `?key=`.
# - Sarvam: API key only. Its OpenAPI spec (https://docs.sarvam.ai/openapi/apis.json) documents the
#   `api-subscription-key` header (and a bearer key), no OAuth flow.
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(id="xai", label="xAI", validate_url="https://api.x.ai/v1/models"),
    ProviderSpec(id="openai", label="OpenAI", validate_url="https://api.openai.com/v1/models"),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        validate_url="https://openrouter.ai/api/v1/key",
        oauth=OAuthSpec(authorize_url="https://openrouter.ai/auth", token_url="https://openrouter.ai/api/v1/auth/keys"),
    ),
    # Vercel AI Gateway keys come from the Vercel dashboard. Validated with
    # `GET https://ai-gateway.vercel.sh/v1/credits` ("Check credit balance",
    # https://vercel.com/docs/ai-gateway/sdks-and-apis/rest-api#check-credit-balance), which needs
    # the key; `GET /v1/models` is public and would accept any key.
    ProviderSpec(
        id="vercel",
        label="Vercel AI Gateway",
        validate_url="https://ai-gateway.vercel.sh/v1/credits",
        oauth=OAuthSpec(
            authorize_url="https://vercel.com/oauth/authorize",
            token_url="https://api.vercel.com/login/oauth/token",
            style="authorization_code",
            scope="openid offline_access",
        ),
    ),
    # Gemini API keys come from Google AI Studio (api_key only) and go in `x-goog-api-key`
    # (https://ai.google.dev/api#authentication); listing models needs the key.
    ProviderSpec(
        id="gemini",
        label="Gemini",
        validate_url="https://generativelanguage.googleapis.com/v1beta/models",
        key_header="x-goog-api-key",
        key_prefix="",
    ),
    # Sarvam keys come from its dashboard and go in `api-subscription-key`. `GET /v2/models`
    # ("List Models V2", ApiKeyAuth in https://docs.sarvam.ai/openapi/apis.json) needs the key.
    ProviderSpec(
        id="sarvam",
        label="Sarvam AI",
        validate_url="https://api.sarvam.ai/v2/models",
        key_header="api-subscription-key",
        key_prefix="",
    ),
)


def find_provider(provider_id: str) -> ProviderSpec | None:
    return next((p for p in PROVIDERS if p.id == provider_id), None)


def provider_label(provider_id: str) -> str:
    """The display name of a provider in PROVIDERS."""
    return next(p.label for p in PROVIDERS if p.id == provider_id)


# Public beta capability notes. They describe the current product, not every API a provider offers.
PROVIDER_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "xai": ("Voice, background agents, and images",),
    "openai": ("Vision and text models",),
    "openrouter": ("Text models",),
    "vercel": ("Text models",),
    "gemini": ("Voice and text models",),
    "sarvam": ("Voice",),
}


class TokenSet(BaseModel):
    """What an `authorization_code` provider's credential holds (as JSON in the SecretStore)."""

    model_config = ConfigDict(extra="forbid")

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_at: float

    def stored_json(self) -> str:
        """The set with its secrets in clear, for the (encrypted) SecretStore only."""
        refresh = self.refresh_token.get_secret_value() if self.refresh_token is not None else None
        return json.dumps({"access_token": self.access_token.get_secret_value(), "refresh_token": refresh, "expires_at": self.expires_at})


class TokenResponse(BaseModel):
    """An OAuth 2 token endpoint response (RFC 6749 section 5.1)."""

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_in: float = 3600.0

    def token_set(self, previous_refresh: SecretStr | None = None) -> TokenSet:
        return TokenSet(
            access_token=self.access_token,
            refresh_token=self.refresh_token or previous_refresh,
            expires_at=time.time() + self.expires_in,
        )


def uses_tokens(provider_id: str) -> bool:
    spec = find_provider(provider_id)
    return spec is not None and spec.oauth is not None and spec.oauth.style == "authorization_code"


class CredentialStore:
    """One owner's view of a `SecretStore`: the only source of provider keys."""

    def __init__(self, store: SecretStore, owner: str = DEFAULT_OWNER) -> None:
        self.store, self.owner = store, owner

    def stored(self, provider_id: str) -> ProviderAuth | None:
        try:
            return self.store.get(self.owner, provider_id)
        except SecretStoreError as exc:
            log.warning("could not read the %s credential: %s", provider_id, exc)
            return None

    def tokens(self, provider_id: str) -> TokenSet | None:
        """The OAuth tokens of a provider signed in with `authorization_code`, if it was."""
        stored = self.stored(provider_id)
        if stored is None or stored.kind != "oauth" or not uses_tokens(provider_id):
            return None
        return TokenSet.model_validate_json(stored.secret.get_secret_value())

    def secret(self, provider_id: str) -> str | None:
        """The stored key (or current OAuth access token) to call `provider_id` with, if any."""
        tokens = self.tokens(provider_id)
        if tokens is not None:
            return tokens.access_token.get_secret_value()
        stored = self.stored(provider_id)
        return stored.secret.get_secret_value() if stored is not None else None

    def connected(self, provider_id: str) -> bool:
        return bool(self.secret(provider_id))

    def save(self, auth: ProviderAuth) -> None:
        try:
            self.store.put(self.owner, auth)
        except SecretStoreError as exc:
            log.error("could not store the %s credential: %s", auth.provider, exc)
            raise HTTPException(502, "the credential store is unavailable") from None

    def remove(self, provider_id: str) -> None:
        try:
            self.store.delete(self.owner, provider_id)
        except SecretStoreError as exc:
            log.error("could not delete the %s credential: %s", provider_id, exc)
            raise HTTPException(502, "the credential store is unavailable") from None


def token_auth(provider_id: str, tokens: TokenSet) -> ProviderAuth:
    return ProviderAuth(provider=provider_id, kind="oauth", secret=SecretStr(tokens.stored_json()))


class TokenRefresher:
    """Refreshes `authorization_code` access tokens shortly before they expire."""

    def __init__(self, store: CredentialStore, client: httpx.AsyncClient, clients: dict[str, OAuthClient]) -> None:
        self.store, self.client, self.clients = store, client, clients

    async def refresh_if_due(self, provider_id: str) -> None:
        tokens = self.store.tokens(provider_id)
        spec = find_provider(provider_id)
        registered = self.clients.get(provider_id)
        if tokens is None or spec is None or spec.oauth is None or registered is None:
            return
        if tokens.refresh_token is None or tokens.expires_at - time.time() > REFRESH_MARGIN_S:
            return
        form = {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token.get_secret_value(), **registered.form()}
        try:
            response = await self.client.post(spec.oauth.token_url, data=form, timeout=VALIDATE_TIMEOUT_S)
        except httpx.HTTPError as exc:
            log.warning("could not reach %s to refresh its token: %s", provider_id, type(exc).__name__)
            return
        if response.status_code != 200:
            log.warning("%s refused the token refresh (HTTP %d); sign in again", provider_id, response.status_code)
            return
        refreshed = TokenResponse.model_validate_json(response.content).token_set(tokens.refresh_token)
        self.store.save(token_auth(provider_id, refreshed))
        log.info("%s access token refreshed", provider_id)


def with_locks(models: tuple[VoiceModel, ...], store: CredentialStore) -> tuple[VoiceModel, ...]:
    """The registry with `locked` set on every model whose provider has no credential."""
    return tuple(m.model_copy(update={"locked": not store.connected(m.provider)}) for m in models)


class ProviderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    authKinds: list[AuthKind]
    connected: bool
    # What a credential can unlock today. A connected provider is not necessarily a voice provider.
    capabilities: list[str] = []


class ProvidersResponse(BaseModel):
    providers: list[ProviderInfo]


class KeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: SecretStr = Field(min_length=1)


class OAuthKeyResponse(BaseModel):
    key: SecretStr


@dataclass(frozen=True)
class PendingOAuth:
    provider: str
    verifier: str
    started: float
    redirect_uri: str = field(default="")


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class OAuthFlows:
    """Started OAuth flows by `state`; each state is single-use and expires after OAUTH_TTL_S."""

    def __init__(self) -> None:
        self.pending: dict[str, PendingOAuth] = {}

    def start(self, provider: str, redirect_uri: str) -> tuple[str, str]:
        now = time.monotonic()
        self.pending = {s: p for s, p in self.pending.items() if now - p.started < OAUTH_TTL_S}
        while len(self.pending) >= OAUTH_PENDING_MAX:
            del self.pending[next(iter(self.pending))]
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        self.pending[state] = PendingOAuth(provider=provider, verifier=verifier, started=now, redirect_uri=redirect_uri)
        return state, verifier

    def finish(self, provider: str, state: str) -> PendingOAuth | None:
        flow = self.pending.pop(state, None)
        if flow is None or flow.provider != provider or time.monotonic() - flow.started >= OAUTH_TTL_S:
            return None
        return flow


def provider_or_404(provider_id: str) -> ProviderSpec:
    spec = find_provider(provider_id)
    if spec is None:
        raise HTTPException(404, f"unknown provider {provider_id!r}")
    return spec


def create_provider_router(
    store: CredentialStore, client: httpx.AsyncClient, storage: Storage, clients: dict[str, OAuthClient]
) -> APIRouter:
    router = APIRouter(prefix="/providers")
    flows = OAuthFlows()

    def oauth_ready(spec: ProviderSpec) -> OAuthSpec | None:
        """The provider's OAuth flow, when it can run here (a registered client for `authorization_code`)."""
        if spec.oauth is None or (spec.oauth.style == "authorization_code" and spec.id not in clients):
            return None
        return spec.oauth

    def kinds(spec: ProviderSpec) -> list[AuthKind]:
        return ["api_key", "oauth"] if oauth_ready(spec) is not None else ["api_key"]

    def require_oauth(spec: ProviderSpec) -> OAuthSpec:
        oauth = oauth_ready(spec)
        if oauth is None:
            raise HTTPException(400, f"{spec.label} OAuth is not available here; use an API key")
        return oauth

    def save(auth: ProviderAuth) -> None:
        store.save(auth)
        storage.save_connection(auth.provider, auth.kind)

    @router.get("")
    async def list_providers() -> ProvidersResponse:
        return ProvidersResponse(
            providers=[
                ProviderInfo(
                    id=p.id,
                    label=p.label,
                    authKinds=kinds(p),
                    connected=store.connected(p.id),
                    capabilities=list(PROVIDER_CAPABILITIES.get(p.id, ())),
                )
                for p in PROVIDERS
            ]
        )

    @router.post("/{provider_id}/key", status_code=204)
    async def connect_key(provider_id: str, body: KeyRequest) -> None:
        spec = provider_or_404(provider_id)
        try:
            response = await client.get(
                spec.validate_url,
                headers={spec.key_header: spec.key_prefix + body.key.get_secret_value()},
                timeout=VALIDATE_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            log.warning("could not reach %s to validate a key: %s", spec.id, type(exc).__name__)
            raise HTTPException(502, f"could not reach {spec.label} to check the key") from None
        if response.status_code != 200:
            log.info("%s rejected a key (HTTP %d)", spec.id, response.status_code)
            raise HTTPException(400, f"{spec.label} rejected this key")
        save(ProviderAuth(provider=spec.id, kind="api_key", secret=body.key))
        log.info("%s connected with an API key", spec.id)

    @router.delete("/{provider_id}", status_code=204)
    async def disconnect(provider_id: str) -> None:
        spec = provider_or_404(provider_id)
        store.remove(spec.id)
        storage.remove_connection(spec.id)

    @router.get("/{provider_id}/oauth/start")
    async def oauth_start(provider_id: str, request: Request) -> RedirectResponse:
        spec = provider_or_404(provider_id)
        oauth = require_oauth(spec)
        callback = str(request.url_for("oauth_callback", provider_id=spec.id))
        state, verifier = flows.start(spec.id, callback)
        if oauth.style == "key_exchange":
            query = httpx.QueryParams(
                callback_url=callback, code_challenge=pkce_challenge(verifier), code_challenge_method="S256", state=state
            )
        else:
            query = httpx.QueryParams(
                client_id=clients[spec.id].client_id,
                redirect_uri=callback,
                response_type="code",
                scope=oauth.scope,
                state=state,
                code_challenge=pkce_challenge(verifier),
                code_challenge_method="S256",
            )
        return RedirectResponse(f"{oauth.authorize_url}?{query}", status_code=302)

    @router.get("/{provider_id}/oauth/callback", name="oauth_callback")
    async def oauth_callback(provider_id: str, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        spec = provider_or_404(provider_id)
        oauth = require_oauth(spec)
        flow = flows.finish(spec.id, state)
        if flow is None:
            log.warning("%s OAuth callback with an unknown or expired state", spec.id)
            raise HTTPException(400, "OAuth state mismatch; start the connection again")
        if error or not code:
            raise HTTPException(400, f"{spec.label} did not authorize Osmo" if error else "OAuth callback without a code")
        try:
            if oauth.style == "key_exchange":
                response = await client.post(
                    oauth.token_url,
                    json={"code": code, "code_verifier": flow.verifier, "code_challenge_method": "S256"},
                    timeout=VALIDATE_TIMEOUT_S,
                )
            else:
                form = {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": flow.verifier,
                    "redirect_uri": flow.redirect_uri,
                    **clients[spec.id].form(),
                }
                response = await client.post(oauth.token_url, data=form, timeout=VALIDATE_TIMEOUT_S)
        except httpx.HTTPError as exc:
            log.warning("could not reach %s to finish OAuth: %s", spec.id, type(exc).__name__)
            raise HTTPException(502, f"could not reach {spec.label}") from None
        if response.status_code != 200:
            log.info("%s refused the OAuth code (HTTP %d)", spec.id, response.status_code)
            raise HTTPException(400, f"{spec.label} refused the authorization")
        if oauth.style == "key_exchange":
            save(ProviderAuth(provider=spec.id, kind="oauth", secret=OAuthKeyResponse.model_validate_json(response.content).key))
        else:
            save(token_auth(spec.id, TokenResponse.model_validate_json(response.content).token_set()))
        log.info("%s connected with OAuth", spec.id)
        return RedirectResponse(f"/#connected={spec.id}", status_code=303)

    return router

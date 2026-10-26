"""Provider credentials: which providers are connected, and the endpoints that connect them.

Secrets live only on the server, in a `SecretStore` (secret_store.py; `OSMO_SECRET_STORE` picks the
Fernet file or Infisical). They are kept as `SecretStr` in memory and never logged or returned by any
endpoint: `GET /providers` reports only whether a provider is connected.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from symphony_server.secret_store import DEFAULT_OWNER, AuthKind, ProviderAuth, SecretStore, SecretStoreError
from symphony_server.voices import VoiceModel

log = logging.getLogger("symphony.providers")

# A started OAuth flow must come back within this many seconds.
OAUTH_TTL_S = 600.0
# Pending OAuth flows kept at once; the oldest are dropped first.
OAUTH_PENDING_MAX = 64
VALIDATE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class OAuthSpec:
    """An OAuth 2 authorization-code + PKCE (S256) flow that yields an API key."""

    authorize_url: str
    token_url: str


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

    @property
    def kinds(self) -> list[AuthKind]:
        return ["api_key", "oauth"] if self.oauth is not None else ["api_key"]


# Only OpenRouter documents an OAuth flow that hands an app an API key for its users
# (https://openrouter.ai/docs/use-cases/oauth-pkce). xAI, OpenAI and Vercel only issue API keys from
# their consoles, so they are api_key only.
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(id="xai", label="xAI", validate_url="https://api.x.ai/v1/models"),
    ProviderSpec(id="openai", label="OpenAI", validate_url="https://api.openai.com/v1/models"),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        validate_url="https://openrouter.ai/api/v1/key",
        oauth=OAuthSpec(authorize_url="https://openrouter.ai/auth", token_url="https://openrouter.ai/api/v1/auth/keys"),
    ),
    # Vercel AI Gateway keys come from the Vercel dashboard (api_key only). Validated with
    # `GET https://ai-gateway.vercel.sh/v1/credits` ("Check credit balance",
    # https://vercel.com/docs/ai-gateway/sdks-and-apis/rest-api#check-credit-balance), which needs
    # the key; `GET /v1/models` is public and would accept any key.
    ProviderSpec(id="vercel", label="Vercel AI Gateway", validate_url="https://ai-gateway.vercel.sh/v1/credits"),
    # Gemini API keys come from Google AI Studio (api_key only) and go in `x-goog-api-key`
    # (https://ai.google.dev/api#authentication); listing models needs the key.
    ProviderSpec(
        id="gemini",
        label="Gemini",
        validate_url="https://generativelanguage.googleapis.com/v1beta/models",
        key_header="x-goog-api-key",
        key_prefix="",
    ),
)


def find_provider(provider_id: str) -> ProviderSpec | None:
    return next((p for p in PROVIDERS if p.id == provider_id), None)


def provider_label(provider_id: str) -> str:
    """The display name of a provider in PROVIDERS."""
    return next(p.label for p in PROVIDERS if p.id == provider_id)


class CredentialStore:
    """One owner's view of a `SecretStore`: the only source of provider keys."""

    def __init__(self, store: SecretStore, owner: str = DEFAULT_OWNER) -> None:
        self.store, self.owner = store, owner

    def secret(self, provider_id: str) -> str | None:
        """The stored key to call `provider_id` with, if any."""
        try:
            stored = self.store.get(self.owner, provider_id)
        except SecretStoreError as exc:
            log.warning("could not read the %s credential: %s", provider_id, exc)
            stored = None
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


def with_locks(models: tuple[VoiceModel, ...], store: CredentialStore) -> tuple[VoiceModel, ...]:
    """The registry with `locked` set on every model whose provider has no credential."""
    return tuple(m.model_copy(update={"locked": not store.connected(m.provider)}) for m in models)


class ProviderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    authKinds: list[AuthKind]
    connected: bool


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


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class OAuthFlows:
    """Started OAuth flows by `state`; each state is single-use and expires after OAUTH_TTL_S."""

    def __init__(self) -> None:
        self.pending: dict[str, PendingOAuth] = {}

    def start(self, provider: str) -> tuple[str, str]:
        now = time.monotonic()
        self.pending = {s: p for s, p in self.pending.items() if now - p.started < OAUTH_TTL_S}
        while len(self.pending) >= OAUTH_PENDING_MAX:
            del self.pending[next(iter(self.pending))]
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        self.pending[state] = PendingOAuth(provider=provider, verifier=verifier, started=now)
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


def create_provider_router(store: CredentialStore, client: httpx.AsyncClient) -> APIRouter:
    router = APIRouter(prefix="/providers")
    flows = OAuthFlows()

    @router.get("")
    async def list_providers() -> ProvidersResponse:
        return ProvidersResponse(
            providers=[ProviderInfo(id=p.id, label=p.label, authKinds=p.kinds, connected=store.connected(p.id)) for p in PROVIDERS]
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
        store.save(ProviderAuth(provider=spec.id, kind="api_key", secret=body.key))
        log.info("%s connected with an API key", spec.id)

    @router.delete("/{provider_id}", status_code=204)
    async def disconnect(provider_id: str) -> None:
        store.remove(provider_or_404(provider_id).id)

    @router.get("/{provider_id}/oauth/start")
    async def oauth_start(provider_id: str, request: Request) -> RedirectResponse:
        spec = provider_or_404(provider_id)
        if spec.oauth is None:
            raise HTTPException(400, f"{spec.label} does not offer OAuth; use an API key")
        state, verifier = flows.start(spec.id)
        callback = str(request.url_for("oauth_callback", provider_id=spec.id))
        query = httpx.QueryParams(
            callback_url=callback, code_challenge=pkce_challenge(verifier), code_challenge_method="S256", state=state
        )
        return RedirectResponse(f"{spec.oauth.authorize_url}?{query}", status_code=302)

    @router.get("/{provider_id}/oauth/callback", name="oauth_callback")
    async def oauth_callback(provider_id: str, code: str = "", state: str = "") -> RedirectResponse:
        spec = provider_or_404(provider_id)
        if spec.oauth is None:
            raise HTTPException(400, f"{spec.label} does not offer OAuth; use an API key")
        flow = flows.finish(spec.id, state)
        if flow is None:
            log.warning("%s OAuth callback with an unknown or expired state", spec.id)
            raise HTTPException(400, "OAuth state mismatch; start the connection again")
        if not code:
            raise HTTPException(400, "OAuth callback without a code")
        try:
            response = await client.post(
                spec.oauth.token_url,
                json={"code": code, "code_verifier": flow.verifier, "code_challenge_method": "S256"},
                timeout=VALIDATE_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            log.warning("could not reach %s to finish OAuth: %s", spec.id, type(exc).__name__)
            raise HTTPException(502, f"could not reach {spec.label}") from None
        if response.status_code != 200:
            log.info("%s refused the OAuth code (HTTP %d)", spec.id, response.status_code)
            raise HTTPException(400, f"{spec.label} refused the authorization")
        key = OAuthKeyResponse.model_validate_json(response.content).key
        store.save(ProviderAuth(provider=spec.id, kind="oauth", secret=key))
        log.info("%s connected with OAuth", spec.id)
        return RedirectResponse(f"/#connected={spec.id}", status_code=303)

    return router

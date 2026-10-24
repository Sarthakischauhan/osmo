"""Provider credentials: which providers are connected, and the endpoints that connect them.

Secrets live only here, on the server. They are kept as `SecretStr` in memory, written to disk
Fernet-encrypted, and never logged or returned by any endpoint: `GET /providers` reports only
whether a provider is connected.

Storage (see CONTRIBUTING.md):
- `OSMO_CREDENTIALS_FILE`: the encrypted file (default `~/.config/osmo/credentials.enc`).
- `OSMO_CREDENTIALS_KEY`: a Fernet key. When unset, a key is generated once into
  `<OSMO_CREDENTIALS_FILE>.key` (mode 0600) and reused.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

from symphony_server.voices import VoiceModel

log = logging.getLogger("symphony.providers")

AuthKind = Literal["api_key", "oauth"]

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
    # Environment variable that also connects this provider (it is never written to the file).
    # Only xAI has one: it is the key Osmo has always booted with.
    env_var: str | None = None

    @property
    def kinds(self) -> list[AuthKind]:
        return ["api_key", "oauth"] if self.oauth is not None else ["api_key"]


# Only OpenRouter documents an OAuth flow that hands an app an API key for its users
# (https://openrouter.ai/docs/use-cases/oauth-pkce). xAI and OpenAI only issue API keys from
# their consoles, so they are api_key only.
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(id="xai", label="xAI", validate_url="https://api.x.ai/v1/models", env_var="XAI_API_KEY"),
    ProviderSpec(id="openai", label="OpenAI", validate_url="https://api.openai.com/v1/models"),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        validate_url="https://openrouter.ai/api/v1/key",
        oauth=OAuthSpec(authorize_url="https://openrouter.ai/auth", token_url="https://openrouter.ai/api/v1/auth/keys"),
    ),
)


def find_provider(provider_id: str) -> ProviderSpec | None:
    return next((p for p in PROVIDERS if p.id == provider_id), None)


class ProviderAuth(BaseModel):
    """One provider's credential. `secret` is a `SecretStr`, so it never shows in a repr or log."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    kind: AuthKind
    secret: SecretStr


_stored_adapter: TypeAdapter[dict[str, ProviderAuth]] = TypeAdapter(dict[str, ProviderAuth])


class CredentialStore:
    """Provider credentials, Fernet-encrypted at rest in `path` (memory only when `path` is None)."""

    def __init__(self, path: Path | None, key: str | None = None, env: dict[str, str] | None = None) -> None:
        self.path = path
        self.env = env or {}
        self.fernet = Fernet(key.encode() if key else self.keyfile_key())
        self.auths: dict[str, ProviderAuth] = self.load()

    def keyfile_key(self) -> bytes:
        if self.path is None:
            return Fernet.generate_key()
        keyfile = self.path.with_name(self.path.name + ".key")
        if keyfile.is_file():
            return keyfile.read_bytes().strip()
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        descriptor = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
        return key

    def load(self) -> dict[str, ProviderAuth]:
        if self.path is None or not self.path.is_file():
            return {}
        try:
            return _stored_adapter.validate_json(self.fernet.decrypt(self.path.read_bytes()))
        except InvalidToken:
            log.error("credentials file %s could not be decrypted with the configured key; ignoring it", self.path)
            return {}

    def save(self) -> None:
        if self.path is None:
            return
        plain = {pid: {"provider": a.provider, "kind": a.kind, "secret": a.secret.get_secret_value()} for pid, a in self.auths.items()}
        token = self.fernet.encrypt(json.dumps(plain).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(token)
        staging.replace(self.path)

    def put(self, auth: ProviderAuth) -> None:
        self.auths[auth.provider] = auth
        self.save()

    def delete(self, provider_id: str) -> None:
        if self.auths.pop(provider_id, None) is not None:
            self.save()

    def secret(self, provider_id: str) -> str | None:
        """The key to call `provider_id` with: a stored one, else its environment variable."""
        stored = self.auths.get(provider_id)
        if stored is not None:
            return stored.secret.get_secret_value()
        spec = find_provider(provider_id)
        return self.env.get(spec.env_var) if spec is not None and spec.env_var else None

    def connected(self, provider_id: str) -> bool:
        return bool(self.secret(provider_id))


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
                headers={"Authorization": f"Bearer {body.key.get_secret_value()}"},
                timeout=VALIDATE_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            log.warning("could not reach %s to validate a key: %s", spec.id, type(exc).__name__)
            raise HTTPException(502, f"could not reach {spec.label} to check the key") from None
        if response.status_code != 200:
            log.info("%s rejected a key (HTTP %d)", spec.id, response.status_code)
            raise HTTPException(400, f"{spec.label} rejected this key")
        store.put(ProviderAuth(provider=spec.id, kind="api_key", secret=body.key))
        log.info("%s connected with an API key", spec.id)

    @router.delete("/{provider_id}", status_code=204)
    async def disconnect(provider_id: str) -> None:
        store.delete(provider_or_404(provider_id).id)

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
        store.put(ProviderAuth(provider=spec.id, kind="oauth", secret=key))
        log.info("%s connected with OAuth", spec.id)
        return RedirectResponse(f"/#connected={spec.id}", status_code=303)

    return router

"""The shared core_ai registries, kept on the provider keys the server currently holds.

Keys come only from the SecretStore (the provider auth flow, `/providers/{id}/key` or OAuth);
the environment is never read for them. `connect` hands the current credentials to core_ai:
the provider's core_ai text and image providers behind the `ProviderRouter` registered for its
namespace on the text/image `ModelRegistry`, and `open_voice` opens core_ai voice sessions
(`core_ai.voice.open_voice_session`, which dispatches by provider) on the API key.

A subscription sign-in (subscriptions.py) serves text only, and is preferred for text when both
it and an API key are connected: signing in is how a user asks for runs on their plan."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable

import httpx

from core_ai import AnthropicProvider, GeminiProvider, GrokProvider, ModelRegistry, OpenAIProvider, VercelProvider
from core_ai.providers.base import BaseProvider
from core_ai.voice import VoiceConfig, VoiceSession, open_voice_session, voice_model
from core_ai.voice.realtime_api import RealtimeApiSession

from symphony_server.grok_compat import install_grok_compat

from symphony_server.provider_router import ProviderRouter
from symphony_server.providers import CredentialStore, TokenRefresher, with_locks
from symphony_server.subscriptions import SubscriptionRefresher, subscription_provider
from symphony_server.voices import CORE_NAMESPACES, VoiceModel, core_model_id, voice_models


log = logging.getLogger("symphony.model_access")

# Each Osmo provider's core_ai text provider, built on its key and the server's xAI base URL.
TEXT_PROVIDERS: dict[str, Callable[[str, str], BaseProvider]] = {
    "xai": lambda key, xai_base_url: GrokProvider(key, base_url=xai_base_url),
    "openai": lambda key, _: OpenAIProvider(key),
    "anthropic": lambda key, _: AnthropicProvider(key),
    "vercel": lambda key, _: VercelProvider(key),
    "gemini": lambda key, _: GeminiProvider(key),
}


class ModelAccess:
    """Voice sessions and previews go through `open_voice`; harness text runs and images through
    `registry`. Keys can be connected or removed at runtime, so callers `connect` a provider
    right before using it; that (re)registers its core_ai providers with the current key."""

    def __init__(
        self,
        registry: ModelRegistry,
        credentials: CredentialStore,
        xai_base_url: str,
        refresher: TokenRefresher | None = None,
        subscriptions: SubscriptionRefresher | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.registry, self.credentials, self.xai_base_url = registry, credentials, xai_base_url
        self.refresher, self.subscriptions, self.http = refresher, subscriptions, http
        # The text models each API key can run, where the provider lists them (xAI), by key digest.
        self.listed: dict[str, frozenset[str]] = {}
        # API keys (voice, images, and text without a subscription), by provider.
        self.keys: dict[str, str] = {}
        # The subscription access token each provider's text runs on, by provider.
        self.tokens: dict[str, str] = {}
        # One router per text namespace, registered once, on the provider's current key (None while it
        # has none). Sarvam serves voice only: core_ai has no Sarvam text provider.
        self.routers = {CORE_NAMESPACES[p]: ProviderRouter(CORE_NAMESPACES[p]) for p in TEXT_PROVIDERS}
        for namespace, router in self.routers.items():
            registry.register(namespace, router)

    def connect(self, provider: str, *, use_subscription: bool = True) -> bool:
        """Point the registries at `provider`'s current credentials; whether it can run text now
        (an API key or a subscription). `voice_ready` says whether it can open voice."""
        key = self.credentials.secret(provider)
        token = self.subscriptions.store.token(provider) if self.subscriptions is not None and use_subscription else None
        access = token.access_token if token is not None else None
        if key == self.keys.get(provider) and access == self.tokens.get(provider):
            return key is not None or access is not None
        router = self.routers.get(CORE_NAMESPACES[provider])
        keyed = TEXT_PROVIDERS[provider](key, self.xai_base_url) if key is not None and router is not None else None
        if router is not None:
            router.images = keyed
            if token is not None and self.subscriptions is not None:
                router.text = subscription_provider(provider, token, self.subscriptions)
            else:
                router.text = keyed
        for held, value in ((self.keys, key), (self.tokens, access)):
            if value is None:
                held.pop(provider, None)
            else:
                held[provider] = value
        return key is not None or access is not None

    async def key_text_models(self, provider: str) -> frozenset[str] | None:
        """The text model ids (and aliases) `provider`'s API key can run, when the provider lists
        them per key; None when unknown. An xAI key may be limited to voice: its team then lists
        no language models, and every text run fails with "does not exist or your team ...".
        Cached per key, so a new or changed key is checked again."""
        key = self.keys.get(provider) if self.connect(provider) else None
        if provider != "xai" or key is None or self.http is None:
            return None
        digest = hashlib.sha256(f"{provider}:{key}".encode()).hexdigest()
        if digest in self.listed:
            return self.listed[digest]
        try:
            response = await self.http.get(
                f"{self.xai_base_url}/language-models", headers={"Authorization": f"Bearer {key}"}, timeout=10.0
            )
            listed = response.json().get("models") if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("could not list the xAI key's text models: %s", type(exc).__name__)
            return None
        if not isinstance(listed, list):
            return None
        ids = frozenset(
            str(name) for model in listed if isinstance(model, dict) for name in (model.get("id"), *(model.get("aliases") or ())) if name
        )
        if not ids:
            log.warning("the xAI key has no text models (its team may be limited to voice); agents use another provider")
        self.listed[digest] = ids
        return ids

    def voice_ready(self, provider: str) -> bool:
        """Whether `provider` has the API key voice sessions need (a subscription is not enough)."""
        return self.connect(provider) and provider in self.keys

    async def prepare(self, provider: str, *, voice: bool = False) -> bool:
        """`connect` (or `voice_ready`, for `voice`), after refreshing the provider's OAuth access
        token and subscription token if they are about to expire."""
        if self.refresher is not None:
            await self.refresher.refresh_if_due(provider)
        if self.subscriptions is not None and not voice:
            token = await asyncio.to_thread(self.subscriptions.current, provider)
            return self.connect(provider, use_subscription=token is not None)
        return self.voice_ready(provider) if voice else self.connect(provider)

    def models(self) -> tuple[VoiceModel, ...]:
        """The picker's models, locked where the provider has no key."""
        return with_locks(voice_models(), self.credentials)

    async def open_voice(self, model: VoiceModel, config: VoiceConfig) -> VoiceSession:
        """Open `model` on its provider's current key; `connect` it first."""
        spec = voice_model(core_model_id(model))
        if spec is None:
            raise LookupError(f"{model.id} is not in the core_ai voice catalog")
        # Only xAI's endpoint is configurable (XAI_BASE_URL); the rest use core_ai's defaults.
        base_url = self.xai_base_url if model.provider == "xai" else None
        session = await open_voice_session(spec, config, self.keys[model.provider], base_url=base_url)
        if isinstance(session, RealtimeApiSession):
            install_grok_compat(session)
        return session

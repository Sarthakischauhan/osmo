"""The shared core_ai registries, kept on the provider keys the server currently holds.

Keys come only from the SecretStore (the provider auth flow, `/providers/{id}/key` or OAuth);
the environment is never read for them. `connect` hands the current key to core_ai:
the provider's core_ai text provider behind the `VoiceRouter` registered for its namespace on the
text/image `ModelRegistry`, and `open_voice` opens core_ai voice sessions
(`core_ai.voice.open_voice_session`, which dispatches by provider) on that key."""

from __future__ import annotations

from collections.abc import Callable

from core_ai import GeminiProvider, GrokProvider, ModelRegistry, OpenAIProvider, VercelProvider
from core_ai.providers.base import BaseProvider
from core_ai.voice import VOICE_MODELS, VoiceConfig, VoiceSession, open_voice_session, voice_model

from symphony_server.providers import CredentialStore, with_locks
from symphony_server.voice_router import VoiceRouter
from symphony_server.voices import CORE_NAMESPACES, VoiceModel, core_model_id, voice_models


# Each Osmo provider's core_ai text provider, built on its key and the server's xAI base URL.
TEXT_PROVIDERS: dict[str, Callable[[str, str], BaseProvider]] = {
    "xai": lambda key, xai_base_url: GrokProvider(key, base_url=xai_base_url),
    "openai": lambda key, _: OpenAIProvider(key),
    "vercel": lambda key, _: VercelProvider(key),
    "gemini": lambda key, _: GeminiProvider(key),
}


class ModelAccess:
    """Voice sessions and previews go through `open_voice`; harness text runs and images through
    `registry`. Keys can be connected or removed at runtime, so callers `connect` a provider
    right before using it; that (re)registers its core_ai providers with the current key."""

    def __init__(
        self, registry: ModelRegistry, credentials: CredentialStore, xai_base_url: str
    ) -> None:
        self.registry, self.credentials, self.xai_base_url = registry, credentials, xai_base_url
        self.keys: dict[str, str] = {}
        # One router per namespace, registered once: voice model turns go to the run's voice link,
        # text and images to the provider's current key (None while it has none).
        voice_ids = frozenset(model.full_id for model in VOICE_MODELS)
        self.routers = {namespace: VoiceRouter(namespace, voice_ids) for namespace in CORE_NAMESPACES.values()}
        for namespace, router in self.routers.items():
            registry.register(namespace, router)

    def connect(self, provider: str) -> bool:
        """Point the registries at `provider`'s current key; False (and disconnected) when it has none."""
        key = self.credentials.secret(provider)
        if key == self.keys.get(provider):
            return key is not None
        namespace = CORE_NAMESPACES[provider]
        if key is None:
            self.routers[namespace].text = None
            del self.keys[provider]
            return False
        self.routers[namespace].text = TEXT_PROVIDERS[provider](key, self.xai_base_url)
        self.keys[provider] = key
        return True

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
        return await open_voice_session(spec, config, self.keys[model.provider], base_url=base_url)

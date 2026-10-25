"""The shared core_ai registry, kept on the provider keys the server currently holds."""

from __future__ import annotations

from core_ai import GrokProvider, ModelRegistry

from symphony_server.providers import CredentialStore, with_locks
from symphony_server.voices import CORE_NAMESPACES, VoiceModel, voice_models


class ModelAccess:
    """Every model call (voice sessions, harness runs, images, previews) goes through `registry`.
    Keys can be connected or removed at runtime, so callers `connect` a provider right before
    using it; that (re)registers its core_ai provider with the current key."""

    def __init__(self, registry: ModelRegistry, credentials: CredentialStore, xai_base_url: str) -> None:
        self.registry, self.credentials, self.xai_base_url = registry, credentials, xai_base_url
        self.keys: dict[str, str] = {}

    def connect(self, provider: str) -> bool:
        """Point the registry at `provider`'s current key; False (and unregistered) when it has none."""
        key = self.credentials.secret(provider)
        if key == self.keys.get(provider):
            return key is not None
        namespace = CORE_NAMESPACES[provider]
        if key is None:
            self.registry.unregister(namespace)
            del self.keys[provider]
            return False
        self.registry.register(namespace, GrokProvider(key, base_url=self.xai_base_url))
        self.keys[provider] = key
        return True

    def models(self) -> tuple[VoiceModel, ...]:
        """The picker's models, locked where the provider has no key."""
        return with_locks(voice_models(self.registry), self.credentials)

"""Voice context windows apply only when known, and voice turns need an open link."""

from __future__ import annotations

import asyncio

import pytest
from core_ai.types import Message

from core_ai import ModelRegistry, OpenAIProvider
from core_ai.voice import VoiceConfig, VoiceModelSpec, VoiceSession
from pydantic import SecretStr
from core_harness.context.state import HarnessState

from symphony_server import model_access
from symphony_server.app import osmo_core_config
from symphony_server.secret_store import DEFAULT_OWNER, ProviderAuth
from symphony_server.model_access import ModelAccess
from symphony_server.providers import CredentialStore
from symphony_server.file_store import FileSecretStore
from symphony_server.settings import Settings
from symphony_server.voice_router import (
    VoiceRouter,
    ProviderNotConnected,
    VoiceSessionNotOpen,
)
from symphony_server.voice_turns import VoiceLinks
from symphony_server.voices import core_model_id, voice_models

VOICE_IDS = frozenset({"grok:grok-voice-latest"})


def run_context_limits(known: dict[str, int]) -> dict[str, int] | None:
    access = ModelAccess(ModelRegistry(), CredentialStore(FileSecretStore(None)), "http://unused/v1")
    return osmo_core_config(Settings(), access, [], VoiceLinks(), known).context_limits


def test_voice_models_without_a_known_window_are_not_trimmed() -> None:
    state = HarnessState(context_limits=run_context_limits({}))
    # Osmo adds no windows of its own: each voice model gets core_ai's known window or none.
    catalog = HarnessState()
    ids = [core_model_id(m) for m in voice_models()]
    assert all(state.context_limit(i) == catalog.context_limit(i) for i in ids)
    assert all(state.context_limit(i) is None for i in ids if i.startswith("grok:"))


def test_a_known_window_is_passed_through() -> None:
    assert run_context_limits({"grok:grok-voice-3.0": 300}) == {"grok:grok-voice-3.0": 300}


async def first_event(router: VoiceRouter, model_name: str) -> None:
    async for _ in router.stream(model_name, [Message(role="user", content="hi")]):
        return


def test_a_voice_model_without_an_open_link_is_refused() -> None:
    with pytest.raises(VoiceSessionNotOpen):
        asyncio.run(first_event(VoiceRouter("grok", VOICE_IDS), "grok-voice-latest"))


def test_text_models_need_a_connected_key() -> None:
    with pytest.raises(ProviderNotConnected):
        asyncio.run(first_event(VoiceRouter("grok", VOICE_IDS), "grok-4.5"))


def test_every_voice_provider_gets_a_router_and_its_own_text_provider() -> None:
    registry = ModelRegistry()
    store = FileSecretStore(None)
    store.put(DEFAULT_OWNER, ProviderAuth(provider="openai", kind="api_key", secret=SecretStr("key")))
    access = ModelAccess(registry, CredentialStore(store), "http://unused/v1")
    assert set(access.routers) == {"grok", "openai", "vercel", "gemini"}
    assert access.connect("openai") and not access.connect("vercel")
    assert isinstance(access.routers["openai"].text, OpenAIProvider)
    with pytest.raises(VoiceSessionNotOpen):
        asyncio.run(first_event(access.routers["openai"], "gpt-realtime"))


def test_open_voice_dispatches_by_provider_and_only_xai_takes_the_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[tuple[str, str, str, str | None]] = []

    async def fake_open(
        spec: VoiceModelSpec, config: VoiceConfig, api_key: str, base_url: str | None = None
    ) -> VoiceSession:
        opened.append((spec.provider, spec.id, api_key, base_url))
        raise ConnectionRefusedError

    monkeypatch.setattr(model_access, "open_voice_session", fake_open)
    store = FileSecretStore(None)
    for provider in ("xai", "openai"):
        store.put(DEFAULT_OWNER, ProviderAuth(provider=provider, kind="api_key", secret=SecretStr(f"{provider}-key")))
    access = ModelAccess(ModelRegistry(), CredentialStore(store), "http://xai.test/v1")
    models = {(m.provider, m.id): m for m in access.models()}
    for key in (("openai", "gpt-realtime"), ("xai", "grok-voice-latest")):
        access.connect(key[0])
        with pytest.raises(ConnectionRefusedError):
            asyncio.run(access.open_voice(models[key], VoiceConfig(voice=models[key].defaultVoice)))
    assert opened == [
        ("openai", "gpt-realtime", "openai-key", None),
        ("grok", "grok-voice-latest", "xai-key", "http://xai.test/v1"),
    ]

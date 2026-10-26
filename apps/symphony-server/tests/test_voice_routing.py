"""Voice context windows apply only when known, and voice turns need an open link."""

from __future__ import annotations

import asyncio

import pytest
from core_ai.types import Message

from core_ai import ModelRegistry
from core_ai.voice import VOICE_MODELS
from core_harness.context.state import HarnessState

from symphony_server.app import osmo_core_config
from symphony_server.model_access import ModelAccess
from symphony_server.providers import CredentialStore
from symphony_server.file_store import FileSecretStore
from symphony_server.settings import Settings
from symphony_server.voice_router import (
    GrokRouter,
    ProviderNotConnected,
    VoiceSessionNotOpen,
)
from symphony_server.voice_turns import VoiceLinks

VOICE_IDS = frozenset({"grok:grok-voice-latest"})


def run_context_limits(known: dict[str, int]) -> dict[str, int] | None:
    access = ModelAccess(ModelRegistry(), CredentialStore(FileSecretStore(None)), "http://unused/v1")
    return osmo_core_config(Settings(), access, [], VoiceLinks(), known).context_limits


def test_voice_models_without_a_known_window_are_not_trimmed() -> None:
    state = HarnessState(context_limits=run_context_limits({}))
    assert all(state.context_limit(m.full_id) is None for m in VOICE_MODELS)


def test_a_known_window_is_passed_through() -> None:
    assert run_context_limits({"grok:grok-voice-3.0": 300}) == {"grok:grok-voice-3.0": 300}


async def first_event(router: GrokRouter, model_name: str) -> None:
    async for _ in router.stream(model_name, [Message(role="user", content="hi")]):
        return


def test_a_voice_model_without_an_open_link_is_refused() -> None:
    with pytest.raises(VoiceSessionNotOpen):
        asyncio.run(first_event(GrokRouter("grok", VOICE_IDS), "grok-voice-latest"))


def test_text_models_need_a_connected_key() -> None:
    with pytest.raises(ProviderNotConnected):
        asyncio.run(first_event(GrokRouter("grok", VOICE_IDS), "grok-4.5"))

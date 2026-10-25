"""Voice context windows apply only when known, and voice turns need an open link."""

from __future__ import annotations

import asyncio

import pytest
from core_ai.types import Message

from core_ai.voice import VOICE_MODELS, VoiceRegistry
from core_harness.context.state import HarnessState

from symphony_server.app import voice_context_limits
from symphony_server.voice_router import (
    GrokRouter,
    ProviderNotConnected,
    VoiceSessionNotOpen,
)

VOICE_IDS = frozenset({"grok:grok-voice-latest"})


def test_voice_models_without_a_known_window_are_not_trimmed() -> None:
    assert voice_context_limits(VoiceRegistry()) == {}
    state = HarnessState(context_limits=voice_context_limits(VoiceRegistry()))
    assert all(state.context_limit(m.full_id) is None for m in VOICE_MODELS)


def test_a_known_window_is_passed_through() -> None:
    voices = VoiceRegistry(context_limits={"grok:grok-voice-3.0": 300})
    assert voice_context_limits(voices) == {"grok:grok-voice-3.0": 300}


async def first_event(router: GrokRouter, model_name: str) -> None:
    async for _ in router.stream(model_name, [Message(role="user", content="hi")]):
        return


def test_a_voice_model_without_an_open_link_is_refused() -> None:
    with pytest.raises(VoiceSessionNotOpen):
        asyncio.run(first_event(GrokRouter("grok", VOICE_IDS), "grok-voice-latest"))


def test_text_models_need_a_connected_key() -> None:
    with pytest.raises(ProviderNotConnected):
        asyncio.run(first_event(GrokRouter("grok", VOICE_IDS), "grok-4.5"))

"""Voice context windows are required server settings, and voice turns need an open link."""

from __future__ import annotations

import asyncio

import pytest
from core_ai.types import Message

from symphony_server.settings import (
    VOICE_CONTEXT_LIMITS_ENV,
    Settings,
    voice_context_limits,
)
from symphony_server.voice_router import (
    GrokRouter,
    ProviderNotConnected,
    VoiceSessionNotOpen,
)

VOICE_IDS = frozenset({"grok:grok-voice-latest"})


def test_voice_context_limits_parse_per_model() -> None:
    assert voice_context_limits(
        "grok:grok-voice-latest=32000, grok:grok-voice-3.0=16000"
    ) == {
        "grok:grok-voice-latest": 32000,
        "grok:grok-voice-3.0": 16000,
    }


@pytest.mark.parametrize(
    "value", [None, "", "grok:grok-voice-latest", "grok:grok-voice-latest=lots"]
)
def test_missing_or_malformed_limits_fail_clearly(value: str | None) -> None:
    with pytest.raises(ValueError, match=VOICE_CONTEXT_LIMITS_ENV):
        voice_context_limits(value)


def test_startup_fails_without_the_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(VOICE_CONTEXT_LIMITS_ENV)
    with pytest.raises(ValueError, match="is required"):
        Settings.from_env()


async def first_event(router: GrokRouter, model_name: str) -> None:
    async for _ in router.stream(model_name, [Message(role="user", content="hi")]):
        return


def test_a_voice_model_without_an_open_link_is_refused() -> None:
    with pytest.raises(VoiceSessionNotOpen):
        asyncio.run(first_event(GrokRouter("grok", VOICE_IDS), "grok-voice-latest"))


def test_text_models_need_a_connected_key() -> None:
    with pytest.raises(ProviderNotConnected):
        asyncio.run(first_event(GrokRouter("grok", VOICE_IDS), "grok-4.5"))

"""Gemini Live closes the socket when setup carries a lowercase thinkingLevel."""

import json

from core_ai.voice.gemini.payload import setup_message
from core_ai.voice.session import VoiceConfig

from symphony_server.gemini_live import normalize_setup, thinking_level


def _setup(model: str, effort: str | None) -> dict:
    frame = json.dumps(setup_message(model, VoiceConfig(voice="Puck", reasoning_effort=effort), None))
    return json.loads(normalize_setup(frame, model))


def test_lowercase_effort_becomes_the_live_enum() -> None:
    assert thinking_level("medium") == "MEDIUM"
    level = _setup("gemini-3.1-flash-live-preview", "medium")["setup"]["generationConfig"]["thinkingConfig"]["thinkingLevel"]
    assert level == "MEDIUM"


def test_minimal_is_the_minimal_enum_gemini_3_1_accepts() -> None:
    level = _setup("gemini-3.1-flash-live-preview", "minimal")["setup"]["generationConfig"]["thinkingConfig"]["thinkingLevel"]
    assert level == "MINIMAL"


def test_gemini_3_8_live_omits_thinking_level() -> None:
    generation = _setup("gemini-3.8-live", "high")["setup"]["generationConfig"]
    assert "thinkingConfig" not in generation


def test_a_model_without_effort_is_left_alone() -> None:
    payload = _setup("gemini-3.5-live-translate-preview", None)
    assert "thinkingConfig" not in payload["setup"]["generationConfig"]


def test_non_setup_frames_pass_through() -> None:
    frame = json.dumps({"realtimeInput": {"audio": {"data": "aa"}}})
    assert normalize_setup(frame, "gemini-3.1-flash-live-preview") == frame
